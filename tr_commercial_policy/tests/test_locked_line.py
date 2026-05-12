# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestLockedLineModel(CommercialPolicyTestCommon):
    """Schema, constraints and chatter for locked lines."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def _create_locked(self, **vals):
        defaults = {
            "condition_id": self.condition.id,
            "applied_on": "product_template",
            "product_tmpl_id": self.product_template_a.id,
            "seller_discount": 5.0,
            "extra_discount": 0.0,
            "fixed_commission_rate": 0.0,
        }
        defaults.update(vals)
        return (
            self.env["partner.commercial.condition.locked.line"]
            .with_user(self.director_user)
            .create(defaults)
        )

    def test_create_locked_line_chatter(self):
        before = len(self.condition.message_ids)
        locked = self._create_locked(seller_discount=7.0, fixed_commission_rate=2.0)
        self.assertTrue(locked.exists())
        after = len(self.condition.message_ids)
        self.assertGreater(after, before, "Chatter entry expected on create")

    def test_write_locked_line_chatter(self):
        locked = self._create_locked()
        before = len(self.condition.message_ids)
        locked.with_user(self.director_user).write({"seller_discount": 12.0})
        after = len(self.condition.message_ids)
        self.assertGreater(after, before, "Chatter entry expected on write")

    def test_unlink_locked_line_chatter(self):
        locked = self._create_locked()
        before = len(self.condition.message_ids)
        locked.with_user(self.director_user).unlink()
        after = len(self.condition.message_ids)
        self.assertGreater(after, before, "Chatter entry expected on unlink")

    def test_fixed_commission_rate_negative_blocked(self):
        with self.assertRaises(ValidationError):
            self._create_locked(fixed_commission_rate=-1.0)

    def test_unique_scope_per_condition_template(self):
        self._create_locked()
        with self.assertRaises(ValidationError):
            self._create_locked()

    def test_unique_scope_per_condition_variant(self):
        self._create_locked(
            applied_on="product",
            product_id=self.product_a.id,
            product_tmpl_id=False,
        )
        with self.assertRaises(ValidationError):
            self._create_locked(
                applied_on="product",
                product_id=self.product_a.id,
                product_tmpl_id=False,
            )

    def test_has_locked_lines_compute(self):
        self.assertFalse(self.condition.has_locked_lines)
        locked = self._create_locked()
        self.condition.invalidate_recordset(["has_locked_lines"])
        self.assertTrue(self.condition.has_locked_lines)
        locked.active = False
        self.condition.invalidate_recordset(["has_locked_lines"])
        self.assertFalse(
            self.condition.has_locked_lines,
            "Archived locked line should not count as active",
        )


@tagged("post_install", "-at_install")
class TestLockedLineCoexistenceConstraint(CommercialPolicyTestCommon):
    """Mutually exclusive coexistence locked × regular."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls.LockedLine = cls.env["partner.commercial.condition.locked.line"]
        cls.Line = cls.env["partner.commercial.condition.line"]

    def _create_regular_line(self, **vals):
        defaults = {
            "condition_id": self.condition.id,
            "applied_on": "product_template",
            "product_tmpl_id": self.product_template_a.id,
            "seller_discount": 3.0,
        }
        defaults.update(vals)
        return self.Line.with_user(self.salesperson).create(defaults)

    def _create_locked(self, **vals):
        defaults = {
            "condition_id": self.condition.id,
            "applied_on": "product_template",
            "product_tmpl_id": self.product_template_a.id,
            "seller_discount": 7.0,
            "fixed_commission_rate": 0.0,
        }
        defaults.update(vals)
        return self.LockedLine.with_user(self.director_user).create(defaults)

    def test_locked_template_blocks_regular_template(self):
        self._create_locked()
        with self.assertRaises(ValidationError):
            self._create_regular_line()

    def test_locked_template_blocks_regular_variant(self):
        self._create_locked()
        with self.assertRaises(ValidationError):
            self._create_regular_line(
                applied_on="product",
                product_id=self.product_a.id,
                product_tmpl_id=False,
            )

    def test_locked_variant_allows_regular_template(self):
        """Locked variant covers only that variant; regular template applies
        to other variants of the same template."""
        self._create_locked(
            applied_on="product",
            product_id=self.product_a.id,
            product_tmpl_id=False,
        )
        # Should NOT raise.
        line = self._create_regular_line()
        self.assertTrue(line.exists())

    def test_regular_blocked_when_locked_already_covers(self):
        """Direction inverse: regular line cannot be created within scope
        of existing locked line."""
        self._create_locked()
        with self.assertRaises(ValidationError):
            self._create_regular_line(
                applied_on="product",
                product_id=self.product_a.id,
                product_tmpl_id=False,
            )


@tagged("post_install", "-at_install")
class TestLockedLineResolution(CommercialPolicyTestCommon):
    """Resolution priority and snapshot population on the order line."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()

    def test_locked_resolves_before_regular(self):
        # Locked variant Y wins over regular template (different scope,
        # so coexistence allowed).
        regular = self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product_template",
                "product_tmpl_id": self.product_template_a.id,
                "seller_discount": 3.0,
            }
        )
        locked = (
            self.env["partner.commercial.condition.locked.line"]
            .with_user(self.director_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product",
                    "product_id": self.product_a.id,
                    "seller_discount": 7.0,
                    "fixed_commission_rate": 0.0,
                }
            )
        )
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        self.assertEqual(line.locked_line_id, locked)
        self.assertAlmostEqual(line.seller_discount, 7.0)
        self.assertAlmostEqual(line.locked_fixed_commission_rate, 0.0)
        self.assertTrue(regular.exists(), "Regular line preserved")

    def test_locked_archived_falls_back_to_regular(self):
        self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product_template",
                "product_tmpl_id": self.product_template_a.id,
                "seller_discount": 3.0,
            }
        )
        locked = (
            self.env["partner.commercial.condition.locked.line"]
            .with_user(self.director_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product",
                    "product_id": self.product_a.id,
                    "seller_discount": 7.0,
                    "fixed_commission_rate": 0.0,
                }
            )
        )
        locked.with_user(self.director_user).active = False
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        self.assertFalse(
            line.locked_line_id,
            "Archived locked must not be applied to new orders",
        )
        self.assertAlmostEqual(line.seller_discount, 3.0)


@tagged("post_install", "-at_install")
class TestLockedLineSecurity(CommercialPolicyTestCommon):
    """ACL: only director can CRUD locked lines."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    @mute_logger("odoo.addons.base.models.ir_model")
    def test_rep_cannot_create_locked(self):
        with self.assertRaises(AccessError):
            self.env["partner.commercial.condition.locked.line"].with_user(
                self.salesperson
            ).create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product_template",
                    "product_tmpl_id": self.product_template_a.id,
                    "seller_discount": 5.0,
                    "fixed_commission_rate": 0.0,
                }
            )

    @mute_logger("odoo.addons.base.models.ir_model")
    def test_manager_cannot_create_locked(self):
        with self.assertRaises(AccessError):
            self.env["partner.commercial.condition.locked.line"].with_user(
                self.manager_user
            ).create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product_template",
                    "product_tmpl_id": self.product_template_a.id,
                    "seller_discount": 5.0,
                    "fixed_commission_rate": 0.0,
                }
            )

    def test_director_can_create_locked(self):
        locked = (
            self.env["partner.commercial.condition.locked.line"]
            .with_user(self.director_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product_template",
                    "product_tmpl_id": self.product_template_a.id,
                    "seller_discount": 5.0,
                    "fixed_commission_rate": 0.0,
                }
            )
        )
        self.assertTrue(locked.exists())


@tagged("post_install", "-at_install")
class TestLockedLineDiscountEdit(CommercialPolicyTestCommon):
    """_check_locked_line_edit: rep blocked, manager/director override."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls.locked = (
            cls.env["partner.commercial.condition.locked.line"]
            .with_user(cls.director_user)
            .create(
                {
                    "condition_id": cls.condition.id,
                    "applied_on": "product_template",
                    "product_tmpl_id": cls.product_template_a.id,
                    "seller_discount": 5.0,
                    "fixed_commission_rate": 0.0,
                }
            )
        )

    def _line_under_locked(self):
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        self.assertEqual(line.locked_line_id, self.locked)
        return line

    def test_rep_blocked_on_seller_discount_write(self):
        line = self._line_under_locked()
        with self.assertRaises(ValidationError):
            line.with_user(self.salesperson).write({"seller_discount": 9.0})

    def test_rep_blocked_on_extra_discount_write(self):
        line = self._line_under_locked()
        with self.assertRaises(ValidationError):
            line.with_user(self.salesperson).write({"extra_discount": 2.0})

    def test_manager_can_override(self):
        line = self._line_under_locked()
        line.with_user(self.manager_user).write({"seller_discount": 9.0})
        self.assertAlmostEqual(line.seller_discount, 9.0)

    def test_director_can_override(self):
        line = self._line_under_locked()
        line.with_user(self.director_user).write({"seller_discount": 12.0})
        self.assertAlmostEqual(line.seller_discount, 12.0)

    def test_programmatic_write_bypasses_check(self):
        line = self._line_under_locked()
        line.with_context(tr_skip_locked_protection=True).write(
            {"seller_discount": 6.0}
        )
        self.assertAlmostEqual(line.seller_discount, 6.0)

    def test_rep_blocked_on_create_with_seller_discount(self):
        order = self._create_order()
        with self.assertRaises(ValidationError):
            self.env["sale.order.line"].with_user(self.salesperson).create(
                {
                    "order_id": order.id,
                    "product_id": self.product_a.id,
                    "product_uom_qty": 1,
                    "seller_discount": 99.0,
                }
            )


@tagged("post_install", "-at_install")
class TestLockedLineCommission(CommercialPolicyTestCommon):
    """Snapshot Float drives commission, not the live locked record."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()
        cls.locked = (
            cls.env["partner.commercial.condition.locked.line"]
            .with_user(cls.director_user)
            .create(
                {
                    "condition_id": cls.condition.id,
                    "applied_on": "product_template",
                    "product_tmpl_id": cls.product_template_a.id,
                    "seller_discount": 5.0,
                    "fixed_commission_rate": 2.0,
                }
            )
        )

    def test_helper_returns_snapshot_for_locked_line(self):
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        self.assertAlmostEqual(line._get_policy_commission_rate(), 2.0)

    def test_director_edits_locked_rate_does_not_affect_existing_line(self):
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        self.assertAlmostEqual(line.locked_fixed_commission_rate, 2.0)
        self.locked.with_user(self.director_user).write({"fixed_commission_rate": 8.0})
        # Snapshot Float on the line is the source of truth.
        self.assertAlmostEqual(line.locked_fixed_commission_rate, 2.0)
        self.assertAlmostEqual(line._get_policy_commission_rate(), 2.0)

    def test_helper_falls_back_to_band_when_no_locked(self):
        # Use product_b (no locked line covering it) so we fall back to
        # band resolution from the agent profile.
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_b, qty=1)
        # seller_discount=3 → fits in the lowest band (10%).
        line.with_context(tr_skip_locked_protection=True).write(
            {"seller_discount": 3.0}
        )
        self.assertFalse(line.locked_line_id)
        rate = line._get_policy_commission_rate()
        self.assertAlmostEqual(rate, 10.0)


@tagged("post_install", "-at_install")
class TestLockedLineDiscountValidation(CommercialPolicyTestCommon):
    """_validate_seller_discount_limit and tier validation bypass."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()

    def test_locked_discount_above_profile_max_passes(self):
        """Locked can define discount above profile band ceiling."""
        # Highest band = 10% in _setup_commission_bands; locked sets 25%.
        self.env["partner.commercial.condition.locked.line"].with_user(
            self.director_user
        ).create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product_template",
                "product_tmpl_id": self.product_template_a.id,
                "seller_discount": 25.0,
                "fixed_commission_rate": 0.0,
            }
        )
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        self.assertAlmostEqual(line.seller_discount, 25.0)
        # Validation must NOT raise even though 25% > profile band 10%.
        line._validate_seller_discount_limit()

    def test_regular_discount_above_profile_max_still_blocked(self):
        order = self._create_order()
        # Regular line: profile cap at 10% (highest band) → 25% must raise.
        with self.assertRaises(ValidationError):
            self._create_order_line(
                order, product=self.product_a, qty=1, seller_discount=25.0
            )

    def test_extra_discount_tier_skipped_when_locked_aligned(self):
        """Aligned locked line skips line-level tier validation issues."""
        self.env["partner.commercial.condition.locked.line"].with_user(
            self.director_user
        ).create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product_template",
                "product_tmpl_id": self.product_template_a.id,
                "seller_discount": 5.0,
                "extra_discount": 10.0,
                "fixed_commission_rate": 0.0,
            }
        )
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        issues = [
            issue
            for issue in order._get_discount_validation_issues()
            if issue["line_id"] == line.id
            and issue["type"] in ("extra_discount", "extra_discount_director")
        ]
        self.assertFalse(
            issues,
            f"Expected no extra_discount tier issue, got: {issues}",
        )


@tagged("post_install", "-at_install")
class TestLockedLineInvoicePropagation(CommercialPolicyTestCommon):
    """_prepare_invoice_line propagates locked snapshots to account.move.line."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()
        cls.locked = (
            cls.env["partner.commercial.condition.locked.line"]
            .with_user(cls.director_user)
            .create(
                {
                    "condition_id": cls.condition.id,
                    "applied_on": "product_template",
                    "product_tmpl_id": cls.product_template_a.id,
                    "seller_discount": 5.0,
                    "fixed_commission_rate": 3.0,
                }
            )
        )

    def test_invoice_line_inherits_locked_snapshot(self):
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        self.assertEqual(line.locked_line_id, self.locked)
        self.assertAlmostEqual(line.locked_fixed_commission_rate, 3.0)
        invoice_vals = line._prepare_invoice_line()
        self.assertEqual(invoice_vals.get("locked_line_id"), self.locked.id)
        self.assertAlmostEqual(invoice_vals.get("locked_fixed_commission_rate"), 3.0)

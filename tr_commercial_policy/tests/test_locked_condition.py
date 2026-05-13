# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Tests for the locked-condition flag-based feature.

Covers core behavior + adversarial scenarios from the threat model
documented in `plano_locked_condition_flag.md`:

- Governance fields (is_locked, fixed_commission_rate, active) are
  director-only across CRUD.
- Locked condition.line is server-side read-only for non-director.
- Overlap constraint between locked and regular condition.line.
- Resolution prefers locked over regular.
- Snapshot fields on sale.order.line are internal (sudo only).
- Discount edit guard uses baseline stored, not live resolution.
- Manager/director override on the order line.
- Post-write reapply when scope moves into locked.
- Invoice propagation via sale_line_ids matches snapshot guard.
"""

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestLockedConditionModel(CommercialPolicyTestCommon):
    """Model-level: governance fields, constraints, chatter."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def _create_line(self, user, **vals):
        defaults = {
            "condition_id": self.condition.id,
            "applied_on": "product_template",
            "product_tmpl_id": self.product_template_a.id,
            "seller_discount": 5.0,
        }
        defaults.update(vals)
        return (
            self.env["partner.commercial.condition.line"]
            .with_user(user)
            .create(defaults)
        )

    def test_director_can_create_locked(self):
        line = self._create_line(
            self.director_user, is_locked=True, fixed_commission_rate=2.0
        )
        self.assertTrue(line.is_locked)
        self.assertAlmostEqual(line.fixed_commission_rate, 2.0)

    def test_rep_blocked_setting_is_locked(self):
        with self.assertRaises(AccessError):
            self._create_line(self.salesperson, is_locked=True)

    def test_manager_blocked_setting_is_locked(self):
        with self.assertRaises(AccessError):
            self._create_line(self.manager_user, is_locked=True)

    def test_rep_blocked_setting_fixed_commission_rate(self):
        with self.assertRaises(AccessError):
            self._create_line(self.salesperson, fixed_commission_rate=2.0)

    def test_rep_blocked_setting_active_false(self):
        with self.assertRaises(AccessError):
            self._create_line(self.salesperson, active=False)

    def test_locked_line_readonly_for_rep_on_write(self):
        line = self._create_line(self.director_user, is_locked=True)
        with self.assertRaises(AccessError):
            line.with_user(self.salesperson).write({"seller_discount": 10.0})

    def test_locked_line_readonly_for_manager_on_write(self):
        """Even manager cannot edit locked condition.line — only override
        the order/invoice line."""
        line = self._create_line(self.director_user, is_locked=True)
        with self.assertRaises(AccessError):
            line.with_user(self.manager_user).write({"seller_discount": 10.0})

    def test_rep_blocked_changing_locked_scope(self):
        """Rep cannot move a locked line to a different product/template."""
        line = self._create_line(self.director_user, is_locked=True)
        with self.assertRaises(AccessError):
            line.with_user(self.salesperson).write(
                {"product_tmpl_id": self.product_template_b.id}
            )

    def test_rep_blocked_deleting_locked(self):
        line = self._create_line(self.director_user, is_locked=True)
        with self.assertRaises(AccessError):
            line.with_user(self.salesperson).unlink()

    def test_fixed_commission_rate_non_negative(self):
        with self.assertRaises(ValidationError):
            self._create_line(
                self.director_user, is_locked=True, fixed_commission_rate=-1.0
            )

    def test_unique_product_consciousness_of_active(self):
        """Archived line in the same scope should not block creating a
        new active line for the same product."""
        archived = self._create_line(self.director_user)
        archived.with_user(self.director_user).active = False
        # Should NOT raise: archived one doesn't count.
        new_line = self._create_line(self.salesperson)
        self.assertTrue(new_line.exists())


@tagged("post_install", "-at_install")
class TestLockedConditionOverlap(CommercialPolicyTestCommon):
    """Coexistence constraint locked × regular."""

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
            "is_locked": True,
            "fixed_commission_rate": 0.0,
        }
        defaults.update(vals)
        return (
            self.env["partner.commercial.condition.line"]
            .with_user(self.director_user)
            .create(defaults)
        )

    def _create_regular(self, **vals):
        defaults = {
            "condition_id": self.condition.id,
            "applied_on": "product_template",
            "product_tmpl_id": self.product_template_a.id,
            "seller_discount": 3.0,
        }
        defaults.update(vals)
        return self.env["partner.commercial.condition.line"].create(defaults)

    def test_locked_template_blocks_regular_template(self):
        self._create_locked()
        with self.assertRaises(ValidationError):
            self._create_regular()

    def test_locked_template_blocks_regular_variant(self):
        self._create_locked()
        with self.assertRaises(ValidationError):
            self._create_regular(
                applied_on="product",
                product_id=self.product_a.id,
                product_tmpl_id=False,
            )

    def test_locked_variant_allows_regular_template(self):
        """Locked variant is narrower; regular template still covers
        other variants of the same template."""
        self._create_locked(
            applied_on="product",
            product_id=self.product_a.id,
            product_tmpl_id=False,
        )
        # Should NOT raise.
        line = self._create_regular()
        self.assertTrue(line.exists())

    def test_regular_blocked_when_locked_covers(self):
        """Direction inverse."""
        self._create_locked()
        with self.assertRaises(ValidationError):
            self._create_regular(
                applied_on="product",
                product_id=self.product_a.id,
                product_tmpl_id=False,
            )

    def test_archived_locked_does_not_block(self):
        locked = self._create_locked()
        locked.with_user(self.director_user).active = False
        # Should NOT raise.
        line = self._create_regular()
        self.assertTrue(line.exists())


@tagged("post_install", "-at_install")
class TestLockedConditionResolution(CommercialPolicyTestCommon):
    """Snapshot population on sale.order.line + locked vs regular
    resolution priority."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()

    def test_locked_resolves_before_regular(self):
        """Locked variant Y wins over regular template X (Y is variant
        of X) — different scope, coexistence allowed."""
        self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product_template",
                "product_tmpl_id": self.product_template_a.id,
                "seller_discount": 3.0,
            }
        )
        locked = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.director_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product",
                    "product_id": self.product_a.id,
                    "seller_discount": 7.0,
                    "is_locked": True,
                    "fixed_commission_rate": 0.0,
                }
            )
        )
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        self.assertEqual(line.locked_condition_line_id, locked)
        self.assertAlmostEqual(line.seller_discount, 7.0)
        self.assertAlmostEqual(line.locked_baseline_seller_discount, 7.0)
        self.assertAlmostEqual(line.locked_fixed_commission_rate, 0.0)

    def test_archived_locked_falls_back_to_regular(self):
        self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product_template",
                "product_tmpl_id": self.product_template_a.id,
                "seller_discount": 3.0,
            }
        )
        locked = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.director_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product",
                    "product_id": self.product_a.id,
                    "seller_discount": 7.0,
                    "is_locked": True,
                    "fixed_commission_rate": 0.0,
                }
            )
        )
        locked.with_user(self.director_user).active = False
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        self.assertFalse(line.locked_condition_line_id)
        self.assertAlmostEqual(line.seller_discount, 3.0)


@tagged("post_install", "-at_install")
class TestLockedConditionDiscountEdit(CommercialPolicyTestCommon):
    """Discount edit guard on sale.order.line: rep blocked, manager+
    override allowed; baseline-based, stable across condition edits."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls.locked = (
            cls.env["partner.commercial.condition.line"]
            .with_user(cls.director_user)
            .create(
                {
                    "condition_id": cls.condition.id,
                    "applied_on": "product_template",
                    "product_tmpl_id": cls.product_template_a.id,
                    "seller_discount": 5.0,
                    "is_locked": True,
                    "fixed_commission_rate": 0.0,
                }
            )
        )

    def _line_under_locked(self):
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        self.assertEqual(line.locked_condition_line_id, self.locked)
        return line

    def test_rep_blocked_writing_divergent_seller_discount(self):
        line = self._line_under_locked()
        with self.assertRaises(ValidationError):
            line.with_user(self.salesperson).write({"seller_discount": 9.0})

    def test_rep_can_write_value_matching_baseline(self):
        """Rep writing exactly the baseline is a noop and must pass."""
        line = self._line_under_locked()
        # Should NOT raise.
        line.with_user(self.salesperson).write(
            {"seller_discount": line.locked_baseline_seller_discount}
        )

    def test_manager_can_override(self):
        line = self._line_under_locked()
        line.with_user(self.manager_user).write({"seller_discount": 9.0})
        self.assertAlmostEqual(line.seller_discount, 9.0)
        # Snapshot stays intact: override is pontual on the order line.
        self.assertEqual(line.locked_condition_line_id, self.locked)

    def test_director_can_override(self):
        line = self._line_under_locked()
        line.with_user(self.director_user).write({"seller_discount": 12.0})
        self.assertAlmostEqual(line.seller_discount, 12.0)

    def test_baseline_stable_after_locked_edited(self):
        """The baseline stored on the order line does NOT change when
        the director edits the locked condition.line after the order
        was opened (decision 8 of the plan)."""
        line = self._line_under_locked()
        original_baseline = line.locked_baseline_seller_discount
        self.locked.with_user(self.director_user).write({"seller_discount": 15.0})
        # The baseline on the line is NOT recomputed.
        self.assertAlmostEqual(line.locked_baseline_seller_discount, original_baseline)
        # Rep still cannot write any value other than the (historical)
        # baseline.
        with self.assertRaises(ValidationError):
            line.with_user(self.salesperson).write({"seller_discount": 15.0})


@tagged("post_install", "-at_install")
class TestLockedConditionSnapshotGuard(CommercialPolicyTestCommon):
    """Snapshot fields are internal — direct write blocked outside
    env.su / base.group_system."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls.locked = (
            cls.env["partner.commercial.condition.line"]
            .with_user(cls.director_user)
            .create(
                {
                    "condition_id": cls.condition.id,
                    "applied_on": "product_template",
                    "product_tmpl_id": cls.product_template_a.id,
                    "seller_discount": 5.0,
                    "is_locked": True,
                    "fixed_commission_rate": 0.0,
                }
            )
        )

    def _line_under_locked(self):
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        return line

    def test_rep_blocked_clearing_locked_condition_line_id(self):
        line = self._line_under_locked()
        with self.assertRaises(AccessError):
            line.with_user(self.salesperson).write({"locked_condition_line_id": False})

    def test_rep_blocked_writing_baseline(self):
        line = self._line_under_locked()
        with self.assertRaises(AccessError):
            line.with_user(self.salesperson).write(
                {"locked_baseline_seller_discount": 99.0}
            )

    def test_manager_blocked_writing_snapshots(self):
        """Manager has override on discount, NOT on snapshots."""
        line = self._line_under_locked()
        with self.assertRaises(AccessError):
            line.with_user(self.manager_user).write({"locked_condition_line_id": False})

    def test_sudo_can_write_snapshots(self):
        """Internal helpers using sudo() pass the guard."""
        line = self._line_under_locked()
        # Should NOT raise.
        line.sudo().write({"locked_fixed_commission_rate": 3.0})
        self.assertAlmostEqual(line.locked_fixed_commission_rate, 3.0)


@tagged("post_install", "-at_install")
class TestLockedConditionCommission(CommercialPolicyTestCommon):
    """_get_policy_commission_rate returns the snapshot Float, not the
    live locked.line value."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()
        cls.locked = (
            cls.env["partner.commercial.condition.line"]
            .with_user(cls.director_user)
            .create(
                {
                    "condition_id": cls.condition.id,
                    "applied_on": "product_template",
                    "product_tmpl_id": cls.product_template_a.id,
                    "seller_discount": 5.0,
                    "is_locked": True,
                    "fixed_commission_rate": 2.0,
                }
            )
        )

    def test_helper_returns_snapshot_for_locked_line(self):
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        self.assertAlmostEqual(line._get_policy_commission_rate(), 2.0)

    def test_snapshot_preserves_commission_after_locked_edited(self):
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        self.assertAlmostEqual(line.locked_fixed_commission_rate, 2.0)
        # Director edits the locked.line AFTER the order was placed.
        self.locked.with_user(self.director_user).write({"fixed_commission_rate": 8.0})
        # Snapshot on the order line does NOT change.
        self.assertAlmostEqual(line.locked_fixed_commission_rate, 2.0)
        self.assertAlmostEqual(line._get_policy_commission_rate(), 2.0)

    def test_helper_falls_back_to_band_when_no_locked(self):
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_b, qty=1)
        line.with_context(tr_skip_price_protection=True).write({"seller_discount": 3.0})
        self.assertFalse(line.locked_condition_line_id)
        rate = line._get_policy_commission_rate()
        self.assertAlmostEqual(rate, 10.0)


@tagged("post_install", "-at_install")
class TestLockedConditionDiscountValidation(CommercialPolicyTestCommon):
    """Locked allows seller_discount above the profile band ceiling
    without raising (bypass of _validate_seller_discount_limit)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()

    def test_locked_discount_above_profile_max_passes(self):
        # Highest band = 10% in _setup_commission_bands; locked sets 25%.
        self.env["partner.commercial.condition.line"].with_user(
            self.director_user
        ).create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product_template",
                "product_tmpl_id": self.product_template_a.id,
                "seller_discount": 25.0,
                "is_locked": True,
                "fixed_commission_rate": 0.0,
            }
        )
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        self.assertAlmostEqual(line.seller_discount, 25.0)
        # Should NOT raise even though 25% > profile band 10%.
        line._validate_seller_discount_limit()

    def test_regular_discount_above_profile_max_still_raises(self):
        order = self._create_order()
        with self.assertRaises(ValidationError):
            self._create_order_line(
                order, product=self.product_a, qty=1, seller_discount=25.0
            )


@tagged("post_install", "-at_install")
class TestLockedConditionInvoicePropagation(CommercialPolicyTestCommon):
    """_prepare_invoice_line propagates the 4 snapshot fields."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()
        cls.locked = (
            cls.env["partner.commercial.condition.line"]
            .with_user(cls.director_user)
            .create(
                {
                    "condition_id": cls.condition.id,
                    "applied_on": "product_template",
                    "product_tmpl_id": cls.product_template_a.id,
                    "seller_discount": 5.0,
                    "is_locked": True,
                    "fixed_commission_rate": 3.0,
                }
            )
        )

    def test_invoice_line_vals_carry_locked_snapshot(self):
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        invoice_vals = line._prepare_invoice_line()
        self.assertEqual(invoice_vals.get("locked_condition_line_id"), self.locked.id)
        self.assertAlmostEqual(invoice_vals.get("locked_fixed_commission_rate"), 3.0)
        self.assertAlmostEqual(invoice_vals.get("locked_baseline_seller_discount"), 5.0)


@tagged("post_install", "-at_install")
class TestLockedConditionAdversarial(CommercialPolicyTestCommon):
    """Adversarial scenarios from the threat model: RPC create paths,
    snapshot spoofing, scope change cleanup, sale→invoice propagation
    after locked edit."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls.locked = (
            cls.env["partner.commercial.condition.line"]
            .with_user(cls.director_user)
            .create(
                {
                    "condition_id": cls.condition.id,
                    "applied_on": "product_template",
                    "product_tmpl_id": cls.product_template_a.id,
                    "seller_discount": 5.0,
                    "is_locked": True,
                    "fixed_commission_rate": 2.0,
                }
            )
        )

    def test_rpc_create_under_locked_without_explicit_discount(self):
        """Rep creates sale order line for a locked product WITHOUT
        sending seller_discount/extra_discount. System must apply the
        canonical discount + snapshot so the line is internally
        consistent (no baseline ≠ seller_discount divergence)."""
        order = self._create_order()
        line = (
            self.env["sale.order.line"]
            .with_user(self.salesperson)
            .create(
                {
                    "order_id": order.id,
                    "product_id": self.product_a.id,
                    "product_uom_qty": 1,
                    "product_uom": self.product_a.uom_id.id,
                    "name": self.product_a.display_name,
                }
            )
        )
        # Snapshot populated.
        self.assertEqual(line.locked_condition_line_id, self.locked)
        # Canonical discount applied (matches the baseline).
        self.assertAlmostEqual(line.seller_discount, 5.0)
        self.assertAlmostEqual(
            line.seller_discount, line.locked_baseline_seller_discount
        )

    def test_rpc_create_with_spoofed_snapshot_is_blocked(self):
        """Rep cannot create a line directly setting snapshot fields."""
        order = self._create_order()
        with self.assertRaises(AccessError):
            self.env["sale.order.line"].with_user(self.salesperson).create(
                {
                    "order_id": order.id,
                    "product_id": self.product_b.id,
                    "product_uom_qty": 1,
                    "name": self.product_b.display_name,
                    "locked_condition_line_id": self.locked.id,
                    "locked_baseline_seller_discount": 99.0,
                    "locked_baseline_extra_discount": 0.0,
                    "locked_fixed_commission_rate": 0.0,
                }
            )

    def test_scope_change_locked_to_regular_clears_snapshot(self):
        """Line was under locked. Rep changes product to one not under
        locked. Snapshot must be cleared, discount must update to the
        regular condition resolution."""
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        self.assertEqual(line.locked_condition_line_id, self.locked)
        # Change product to one NOT under locked.
        line.with_user(self.salesperson).write({"product_id": self.product_b.id})
        # Snapshot must be cleared.
        self.assertFalse(line.locked_condition_line_id)
        self.assertAlmostEqual(line.locked_baseline_seller_discount, 0.0)
        self.assertAlmostEqual(line.locked_fixed_commission_rate, 0.0)

    def test_sale_to_invoice_propagation_after_locked_edited(self):
        """Sale line was created under locked; director edits the
        locked condition.line afterward; ``_prepare_invoice_line``
        propagates the historical sale snapshot, NOT the live
        condition values."""
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        original_baseline = line.locked_baseline_seller_discount
        # Director edits the locked AFTER the sale snapshot.
        self.locked.with_user(self.director_user).write(
            {"seller_discount": 15.0, "fixed_commission_rate": 8.0}
        )
        # Sale line snapshot is intact (historical truth).
        self.assertAlmostEqual(line.locked_baseline_seller_discount, original_baseline)
        invoice_vals = line._prepare_invoice_line()
        self.assertAlmostEqual(
            invoice_vals.get("locked_baseline_seller_discount"),
            original_baseline,
        )
        self.assertAlmostEqual(invoice_vals.get("locked_fixed_commission_rate"), 2.0)

    def test_invoice_create_cross_product_spoof_blocked(self):
        """Rep cannot create an invoice line for product B pointing
        ``sale_line_ids`` to a sale.order.line of product A (which is
        under locked) to inherit its snapshot. Defends the
        ``_snapshots_match_sale_origin`` propagation exception against
        cross-product spoofing.
        """
        order = self._create_order()
        sale_line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(sale_line, self.condition)
        self.assertEqual(sale_line.locked_condition_line_id, self.locked)
        # Build an invoice for the same partner so the move is valid.
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.customer.id,
            }
        )
        # Spoof attempt A: product mismatch — invoice line of product B
        # claiming snapshot from sale line of product A.
        with self.assertRaises(AccessError):
            self.env["account.move.line"].with_user(self.salesperson).create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_b.id,
                    "quantity": 1,
                    "name": self.product_b.display_name,
                    "sale_line_ids": [(4, sale_line.id)],
                    "locked_condition_line_id": (sale_line.locked_condition_line_id.id),
                    "locked_fixed_commission_rate": (
                        sale_line.locked_fixed_commission_rate
                    ),
                    "locked_baseline_seller_discount": (
                        sale_line.locked_baseline_seller_discount
                    ),
                    "locked_baseline_extra_discount": (
                        sale_line.locked_baseline_extra_discount
                    ),
                }
            )
        # Spoof attempt B: product_id ausente — payload sem product_id
        # também deve bloquear (sem isso, write subsequente pode definir
        # product_id de outro e o snapshot fica vinculado errado).
        with self.assertRaises(AccessError):
            self.env["account.move.line"].with_user(self.salesperson).create(
                {
                    "move_id": invoice.id,
                    "quantity": 1,
                    "name": self.product_b.display_name,
                    "sale_line_ids": [(4, sale_line.id)],
                    "locked_condition_line_id": (sale_line.locked_condition_line_id.id),
                    "locked_fixed_commission_rate": (
                        sale_line.locked_fixed_commission_rate
                    ),
                    "locked_baseline_seller_discount": (
                        sale_line.locked_baseline_seller_discount
                    ),
                    "locked_baseline_extra_discount": (
                        sale_line.locked_baseline_extra_discount
                    ),
                }
            )

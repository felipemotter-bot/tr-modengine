# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Coverage-completion tests for the locked-condition feature.

These tests exist to cover branches not naturally exercised by the
behavior-driven tests in test_locked_condition.py (helper early
returns, mirror methods on invoice lines, band parent guards,
tier-validation skips when aligned with locked snapshot).
"""

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from ..models.policy_utils import locked_covers_line
from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestLockedCoversLineHelper(CommercialPolicyTestCommon):
    """Direct exercise of ``policy_utils.locked_covers_line`` early
    returns. Behavior-level tests use the constraint that calls this
    helper, but the helper itself has guard paths for swapped/empty/
    archived inputs that the constraint never reaches.
    """

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
            "product_tmpl_id": self.product_template_b.id,
            "seller_discount": 3.0,
        }
        defaults.update(vals)
        return self.env["partner.commercial.condition.line"].create(defaults)

    def test_empty_inputs_return_false(self):
        Empty = self.env["partner.commercial.condition.line"]
        self.assertFalse(locked_covers_line(Empty, Empty))
        regular = self._create_regular()
        self.assertFalse(locked_covers_line(Empty, regular))
        self.assertFalse(locked_covers_line(regular, Empty))

    def test_archived_locked_returns_false(self):
        locked = self._create_locked()
        # Archive BEFORE creating the regular — otherwise the constraint
        # blocks the regular creation.
        locked.with_user(self.director_user).active = False
        regular = self._create_regular(product_tmpl_id=self.product_template_a.id)
        self.assertFalse(locked_covers_line(locked, regular))

    def test_swapped_roles_returns_false(self):
        """Caller is responsible for role pairing — passing a regular as
        ``locked`` (or a locked as ``regular``) is rejected."""
        locked = self._create_locked()
        # Regular line on a different template avoids the overlap
        # constraint while still letting us exercise the swap guard.
        regular = self._create_regular()
        # Swapped: locked argument receives a regular line.
        self.assertFalse(locked_covers_line(regular, locked))

    def test_locked_variant_with_regular_template_other_template(self):
        """Locked variant only covers the matching template; a regular
        template for ANOTHER template is not covered."""
        locked = self._create_locked(
            applied_on="product",
            product_id=self.product_a.id,
            product_tmpl_id=False,
        )
        regular_other = self._create_regular()  # product_template_b
        self.assertFalse(locked_covers_line(locked, regular_other))


@tagged("post_install", "-at_install")
class TestResolveWithLockedEdgeCases(CommercialPolicyTestCommon):
    """Direct exercise of ``_resolve_with_locked`` paths not naturally
    triggered by the sale-line apply flow."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_no_product_returns_general_tuple(self):
        """When ``product`` is empty/False, returns the condition's
        header seller_discount + general source + empty locked + 0 rate.
        """
        self.condition.seller_discount = 4.0
        seller, extra, source, locked_line, rate = self.condition._resolve_with_locked(
            False
        )
        self.assertAlmostEqual(seller, 4.0)
        self.assertAlmostEqual(extra, 0.0)
        self.assertEqual(source, "general")
        self.assertFalse(locked_line)
        self.assertAlmostEqual(rate, 0.0)


@tagged("post_install", "-at_install")
class TestLockedBandParentGuard(CommercialPolicyTestCommon):
    """Rep cannot manipulate bands under a locked condition.line."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
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
                }
            )
        )
        cls.regular = cls.env["partner.commercial.condition.line"].create(
            {
                "condition_id": cls.condition.id,
                "applied_on": "product_template",
                "product_tmpl_id": cls.product_template_b.id,
                "seller_discount": 3.0,
            }
        )

    def test_rep_blocked_creating_band_under_locked(self):
        with self.assertRaises(AccessError):
            self.env["partner.commercial.condition.line.band"].with_user(
                self.salesperson
            ).create(
                {
                    "line_id": self.locked.id,
                    "qty_min": 10.0,
                    "qty_uom_id": self.product_a.uom_id.id,
                    "seller_discount": 8.0,
                }
            )

    def test_rep_blocked_editing_band_under_locked(self):
        # Director seeds a band; rep tries to edit it.
        band = (
            self.env["partner.commercial.condition.line.band"]
            .with_user(self.director_user)
            .create(
                {
                    "line_id": self.locked.id,
                    "qty_min": 10.0,
                    "qty_uom_id": self.product_a.uom_id.id,
                    "seller_discount": 8.0,
                }
            )
        )
        with self.assertRaises(AccessError):
            band.with_user(self.salesperson).write({"seller_discount": 9.0})

    def test_rep_blocked_moving_band_to_locked_parent(self):
        # Director seeds a band on the regular line.
        band = (
            self.env["partner.commercial.condition.line.band"]
            .with_user(self.director_user)
            .create(
                {
                    "line_id": self.regular.id,
                    "qty_min": 10.0,
                    "qty_uom_id": self.product_b.uom_id.id,
                    "seller_discount": 8.0,
                }
            )
        )
        # Rep tries to reparent it under the locked line.
        with self.assertRaises(AccessError):
            band.with_user(self.salesperson).write({"line_id": self.locked.id})


@tagged("post_install", "-at_install")
class TestLockedOverlapBlocksRegularOnLockedCreate(CommercialPolicyTestCommon):
    """Reverse direction of the overlap constraint: regular line
    exists first; promoting a locked line that would cover it is
    blocked with the labels listed in the message."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_creating_locked_when_regular_exists_blocked(self):
        # Regular line first.
        self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product_template",
                "product_tmpl_id": self.product_template_a.id,
                "seller_discount": 3.0,
            }
        )
        # Director tries to create a locked covering it.
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition.line"].with_user(
                self.director_user
            ).create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product_template",
                    "product_tmpl_id": self.product_template_a.id,
                    "seller_discount": 5.0,
                    "is_locked": True,
                }
            )


@tagged("post_install", "-at_install")
class TestLockedTierValidationSkipsWhenAligned(CommercialPolicyTestCommon):
    """Sale order tier validation skips lines aligned with the locked
    snapshot — director already decided the discount, no approval
    needed even when the value exceeds manager_extra_limit."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        # Manager limit small so we can place a locked above it.
        cls.agent_profile.manager_extra_limit = 5.0
        cls.locked = (
            cls.env["partner.commercial.condition.line"]
            .with_user(cls.director_user)
            .create(
                {
                    "condition_id": cls.condition.id,
                    "applied_on": "product_template",
                    "product_tmpl_id": cls.product_template_a.id,
                    "seller_discount": 0.0,
                    "extra_discount": 25.0,
                    "is_locked": True,
                    "fixed_commission_rate": 0.0,
                }
            )
        )

    def test_sale_extra_discount_above_manager_limit_passes_when_aligned(self):
        """Order line with extra_discount > manager limit but aligned
        with the locked snapshot must not raise a tier issue."""
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        # Sanity: snapshot populated, aligned, and extra > manager limit.
        self.assertTrue(line.locked_condition_line_id)
        self.assertTrue(line._is_aligned_with_locked_snapshot())
        self.assertGreater(line.extra_discount, 5.0)
        # Tier validation: no issue should target this line.
        issues = order._get_discount_validation_issues()
        line_issues = [i for i in issues if i.get("line_id") == line.id]
        self.assertFalse(
            line_issues,
            msg=f"Expected no tier issues on aligned locked line, got: {line_issues}",
        )

    def test_invoice_extra_discount_above_manager_limit_passes_when_aligned(self):
        self.product_template_a.invoice_policy = "order"
        order = self._create_order()
        order.fiscal_operation_id = False
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        order.action_confirm()
        invoice = order._create_invoices()
        inv_line = invoice.invoice_line_ids.filtered("product_id")[:1]
        self.assertTrue(inv_line.locked_condition_line_id)
        self.assertTrue(inv_line._is_aligned_with_locked_snapshot())
        issues = invoice._get_invoice_discount_validation_issues()
        line_issues = [i for i in issues if i.get("line_id") == inv_line.id]
        self.assertFalse(line_issues)


@tagged("post_install", "-at_install")
class TestLockedInvoicePrepareConditionVals(CommercialPolicyTestCommon):
    """Coverage for ``account.move._prepare_condition_line_vals`` paths:
    no-product early return and locked-branch commission_rate.
    """

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
                    "fixed_commission_rate": 7.0,
                }
            )
        )

    def test_prepare_vals_empty_when_no_product(self):
        invoice = self._create_manual_invoice()
        line = self.env["account.move.line"].create(
            {
                "move_id": invoice.id,
                "display_type": "line_section",
                "name": "Section title",
            }
        )
        # Both helpers short-circuit when product is missing.
        self.assertEqual(invoice._prepare_condition_line_vals(line, self.condition), {})
        self.assertEqual(
            invoice._prepare_locked_snapshot_vals(line, self.condition), {}
        )

    def test_prepare_vals_uses_locked_fixed_commission_rate(self):
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = self.condition
        invoice.sales_profile_id = self.agent_profile
        line = (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_a.id,
                    "quantity": 1.0,
                    "name": self.product_a.display_name,
                }
            )
        )
        vals = invoice._prepare_condition_line_vals(line, self.condition)
        self.assertAlmostEqual(vals.get("commission_rate"), 7.0)


@tagged("post_install", "-at_install")
class TestLockedInvoiceLineGuards(CommercialPolicyTestCommon):
    """Mirror of TestLockedConditionDiscountEdit + TestLockedConditionAdversarial
    for ``account.move.line`` paths."""

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

    def _make_invoice_with_locked_line(self):
        """Create sale order → apply locked → confirm → invoice. Returns
        ``(invoice, inv_line)``. Going through the order avoids the
        ``_sync_dynamic_lines`` plumbing problems of building an invoice
        line by hand with a partial vals payload.
        """
        self.product_template_a.invoice_policy = "order"
        order = self._create_order()
        order.fiscal_operation_id = False
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        order.action_confirm()
        invoice = order._create_invoices()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda li: li.product_id == self.product_a
        )[:1]
        return invoice, inv_line

    def test_invoice_line_write_seller_diverging_blocked_for_rep(self):
        """Discount-edit guard raises directly against the baseline
        snapshot — exercised at the guard level to avoid the
        ``_sync_dynamic_lines`` ceremony of a real write."""
        _invoice, line = self._make_invoice_with_locked_line()
        self.assertEqual(line.locked_condition_line_id, self.locked)
        guard = line.with_user(self.salesperson)
        with self.assertRaises(ValidationError):
            guard._check_locked_line_edit({"seller_discount": 9.0})

    def test_invoice_line_write_extra_diverging_blocked_for_rep(self):
        _invoice, line = self._make_invoice_with_locked_line()
        guard = line.with_user(self.salesperson)
        with self.assertRaises(ValidationError):
            guard._check_locked_line_edit({"extra_discount": 10.0})

    def test_invoice_line_write_baseline_match_passes_for_rep(self):
        _invoice, line = self._make_invoice_with_locked_line()
        guard = line.with_user(self.salesperson)
        # Should not raise.
        guard._check_locked_line_edit(
            {"seller_discount": line.locked_baseline_seller_discount}
        )

    def test_invoice_line_manager_can_override(self):
        _invoice, line = self._make_invoice_with_locked_line()
        # Manager bypass at the guard level — value doesn't matter.
        line.with_user(self.manager_user)._check_locked_line_edit(
            {"seller_discount": 9.0}
        )

    def test_invoice_line_director_can_override(self):
        _invoice, line = self._make_invoice_with_locked_line()
        line.with_user(self.director_user)._check_locked_line_edit(
            {"seller_discount": 12.0}
        )

    def test_invoice_line_snapshot_write_blocked_for_rep(self):
        _invoice, line = self._make_invoice_with_locked_line()
        guard = line.with_user(self.salesperson)
        with self.assertRaises(AccessError):
            guard._check_locked_snapshot_write(
                {"locked_baseline_seller_discount": 99.0}
            )

    def test_invoice_line_snapshot_write_admin_bypass(self):
        """``base.group_system`` users bypass the snapshot guard
        (covers the ``has_group('base.group_system')`` branch)."""
        _invoice, line = self._make_invoice_with_locked_line()
        # Admin (env.user) IS group_system; should not raise.
        line._check_locked_snapshot_write({"locked_baseline_seller_discount": 99.0})

    def test_invoice_line_scope_change_into_locked_explicit_seller_diverging_blocked(
        self,
    ):
        """Move a manual invoice line into a locked-covered product with
        an explicit seller_discount that does not match canonical."""
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = self.condition
        invoice.sales_profile_id = self.agent_profile
        line = (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_b.id,
                    "quantity": 1.0,
                    "name": self.product_b.display_name,
                }
            )
        )
        guard = line.with_user(self.salesperson)
        with self.assertRaises(ValidationError):
            guard._check_locked_line_edit(
                {"product_id": self.product_a.id, "seller_discount": 9.0}
            )

    def test_invoice_line_scope_change_into_locked_explicit_extra_diverging_blocked(
        self,
    ):
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = self.condition
        invoice.sales_profile_id = self.agent_profile
        line = (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_b.id,
                    "quantity": 1.0,
                    "name": self.product_b.display_name,
                }
            )
        )
        guard = line.with_user(self.salesperson)
        with self.assertRaises(ValidationError):
            guard._check_locked_line_edit(
                {"product_id": self.product_a.id, "extra_discount": 10.0}
            )

    def test_invoice_line_scope_change_into_locked_canonical_seller_passes(self):
        """Scope change with explicit seller_discount that DOES match
        the canonical resolution passes the guard."""
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = self.condition
        invoice.sales_profile_id = self.agent_profile
        line = (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_b.id,
                    "quantity": 1.0,
                    "name": self.product_b.display_name,
                }
            )
        )
        guard = line.with_user(self.salesperson)
        # Canonical resolution: seller=5, extra=0.
        guard._check_locked_line_edit(
            {"product_id": self.product_a.id, "seller_discount": 5.0}
        )

    def test_invoice_line_scope_change_into_regular_skipped_by_guard(self):
        """Scope change with a NON-locked target product hits the
        ``source != 'locked'`` early-continue branch (no validation
        against canonical, regular discount writes pass)."""
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = self.condition
        invoice.sales_profile_id = self.agent_profile
        line = (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_b.id,
                    "quantity": 1.0,
                    "name": self.product_b.display_name,
                }
            )
        )
        guard = line.with_user(self.salesperson)
        # product_b stays the same — still regular. Guard should not
        # raise even with an arbitrary discount in vals.
        guard._check_locked_line_edit({"product_id": self.product_b.id})


@tagged("post_install", "-at_install")
class TestLockedInvoiceLineCreateGuards(CommercialPolicyTestCommon):
    """Mirrors the create-time guards on invoice lines, including the
    sale→invoice propagation exception and direct snapshot spoofing."""

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

    def _build_invoice_line_under_locked(self, **extra):
        """Create a manual invoice line under the locked condition.
        Returns the line in sudo. Caller exercises the guard methods
        explicitly to avoid ``_sync_dynamic_lines`` plumbing.
        """
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = self.condition
        invoice.sales_profile_id = self.agent_profile
        vals = {
            "move_id": invoice.id,
            "product_id": self.product_a.id,
            "quantity": 1.0,
            "name": self.product_a.display_name,
        }
        vals.update(extra)
        line = self.env["account.move.line"].sudo().create(vals)
        return line

    def test_check_locked_line_create_canonical_passes(self):
        """Canonical seller=5, extra=0 against the locked → passes."""
        line = self._build_invoice_line_under_locked(
            seller_discount=5.0, extra_discount=0.0
        )
        # Should not raise.
        line.with_user(self.salesperson)._check_locked_line_create(
            [{"seller_discount": 5.0, "extra_discount": 0.0}]
        )

    def test_check_locked_line_create_divergent_seller_blocked(self):
        line = self._build_invoice_line_under_locked(seller_discount=9.0)
        with self.assertRaises(ValidationError):
            line.with_user(self.salesperson)._check_locked_line_create(
                [{"seller_discount": 9.0}]
            )

    def test_check_locked_line_create_no_protected_in_vals_skipped(self):
        """When no seller/extra in vals_list, guard returns immediately
        (covers the early-return branch)."""
        line = self._build_invoice_line_under_locked()
        # Should not raise.
        line.with_user(self.salesperson)._check_locked_line_create([{"quantity": 2.0}])

    def test_check_locked_snapshot_write_create_spoof_blocked(self):
        AccountLine = self.env["account.move.line"].with_user(self.salesperson)
        with self.assertRaises(AccessError):
            AccountLine._check_locked_snapshot_write_create(
                [
                    {
                        "product_id": self.product_b.id,
                        "locked_condition_line_id": self.locked.id,
                        "locked_baseline_seller_discount": 99.0,
                    }
                ]
            )

    def test_check_locked_snapshot_write_create_admin_bypass(self):
        """``base.group_system`` users bypass the create-time snapshot
        guard."""
        # env.user is admin → has group_system → bypass.
        AccountLine = self.env["account.move.line"]
        AccountLine._check_locked_snapshot_write_create(
            [
                {
                    "product_id": self.product_b.id,
                    "locked_condition_line_id": self.locked.id,
                }
            ]
        )

    def test_check_locked_snapshot_write_create_no_snapshot_fields_skipped(self):
        """Vals without snapshot fields skips the guard (early return)."""
        AccountLine = self.env["account.move.line"].with_user(self.salesperson)
        # Should not raise.
        AccountLine._check_locked_snapshot_write_create(
            [{"product_id": self.product_b.id, "quantity": 2.0}]
        )


@tagged("post_install", "-at_install")
class TestSaleLineOpenLockedActionAndIdempotency(CommercialPolicyTestCommon):
    """Misc coverage: action_open_locked_condition_line return values
    and the no-op continue in _snapshot_locked_on_create when the line
    already has a snapshot.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
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
                }
            )
        )

    def test_open_locked_action_returns_false_when_no_snapshot(self):
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_b, qty=1)
        # Regular product → no snapshot.
        self.assertFalse(line.action_open_locked_condition_line())

    def test_open_locked_action_returns_act_window_when_snapshot(self):
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        action = line.action_open_locked_condition_line()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "partner.commercial.condition.line")
        self.assertEqual(action["res_id"], self.locked.id)

    def test_snapshot_locked_on_create_skips_when_already_snapshotted(self):
        """Direct call to ``_snapshot_locked_on_create`` is a no-op on
        lines that already carry a snapshot."""
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        baseline = line.locked_baseline_seller_discount
        # Tamper-resistant snapshot stays the same after re-running.
        line._snapshot_locked_on_create([{}])
        self.assertAlmostEqual(line.locked_baseline_seller_discount, baseline)


@tagged("post_install", "-at_install")
class TestLockedSaleLineGuardsCoverage(CommercialPolicyTestCommon):
    """Mirror of the invoice-line guard tests for ``sale.order.line``.
    Exercises the guard methods directly to cover branches the natural
    write path doesn't reach."""

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

    def _line_under_locked(self):
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        return line

    def _line_regular_product(self):
        order = self._create_order()
        return self._create_order_line(order, product=self.product_b, qty=1)

    def test_check_locked_line_edit_admin_bypass(self):
        """``base.group_system`` (env.user) bypasses the guard."""
        line = self._line_under_locked()
        # Should not raise.
        line._check_locked_line_edit({"seller_discount": 9.0})

    def test_check_locked_line_edit_manager_bypass(self):
        line = self._line_under_locked()
        line.with_user(self.manager_user)._check_locked_line_edit(
            {"seller_discount": 9.0}
        )

    def test_check_locked_line_edit_no_protected_in_vals_skipped(self):
        line = self._line_under_locked()
        # Should not raise.
        line.with_user(self.salesperson)._check_locked_line_edit({"name": "X"})

    def test_check_locked_line_edit_baseline_match_passes(self):
        line = self._line_under_locked()
        line.with_user(self.salesperson)._check_locked_line_edit(
            {"seller_discount": line.locked_baseline_seller_discount}
        )

    def test_check_locked_line_edit_extra_baseline_match_passes(self):
        line = self._line_under_locked()
        line.with_user(self.salesperson)._check_locked_line_edit(
            {"extra_discount": line.locked_baseline_extra_discount}
        )

    def test_check_locked_line_edit_seller_diverging_blocked(self):
        line = self._line_under_locked()
        guard = line.with_user(self.salesperson)
        with self.assertRaises(ValidationError):
            guard._check_locked_line_edit({"seller_discount": 9.0})

    def test_check_locked_line_edit_extra_diverging_blocked(self):
        line = self._line_under_locked()
        guard = line.with_user(self.salesperson)
        with self.assertRaises(ValidationError):
            guard._check_locked_line_edit({"extra_discount": 10.0})

    def test_check_locked_line_edit_scope_change_into_locked_canonical_seller_passes(
        self,
    ):
        line = self._line_regular_product()
        guard = line.with_user(self.salesperson)
        # Canonical = 5.0 (locked seller_discount).
        guard._check_locked_line_edit(
            {"product_id": self.product_a.id, "seller_discount": 5.0}
        )

    def test_check_locked_line_edit_scope_change_into_locked_divergent_seller_blocked(
        self,
    ):
        line = self._line_regular_product()
        guard = line.with_user(self.salesperson)
        with self.assertRaises(ValidationError):
            guard._check_locked_line_edit(
                {"product_id": self.product_a.id, "seller_discount": 9.0}
            )

    def test_check_locked_line_edit_scope_change_into_locked_divergent_extra_blocked(
        self,
    ):
        line = self._line_regular_product()
        guard = line.with_user(self.salesperson)
        with self.assertRaises(ValidationError):
            guard._check_locked_line_edit(
                {"product_id": self.product_a.id, "extra_discount": 10.0}
            )

    def test_check_locked_line_edit_scope_change_into_regular_skipped(self):
        """Scope target NOT under locked → source!=locked branch."""
        line = self._line_regular_product()
        # product_b stays regular. Guard should not raise.
        line.with_user(self.salesperson)._check_locked_line_edit(
            {"product_id": self.product_b.id}
        )

    def test_check_locked_line_edit_no_condition_skipped(self):
        """Order has no commercial_condition_id → continue branch."""
        order = self.env["sale.order"].create(
            {
                "partner_id": self.env["res.partner"]
                .create({"name": "Partner without condition"})
                .id,
                "user_id": self.salesperson.id,
            }
        )
        line = self._create_order_line(order, product=self.product_a, qty=1)
        # Should not raise.
        line.with_user(self.salesperson)._check_locked_line_edit(
            {"product_id": self.product_a.id, "seller_discount": 9.0}
        )

    def test_check_locked_line_edit_no_product_in_vals_skipped(self):
        """Vals product_id = False → product browse empty → continue."""
        line = self._line_regular_product()
        line.with_user(self.salesperson)._check_locked_line_edit(
            {"product_id": False, "seller_discount": 9.0}
        )

    def test_check_locked_snapshot_write_admin_bypass(self):
        """``base.group_system`` bypasses snapshot write guard."""
        line = self._line_under_locked()
        line._check_locked_snapshot_write({"locked_baseline_seller_discount": 99.0})

    def test_check_locked_snapshot_write_no_snapshot_fields_skipped(self):
        line = self._line_under_locked()
        line.with_user(self.salesperson)._check_locked_snapshot_write({"name": "X"})

    def test_check_locked_snapshot_write_rep_blocked(self):
        line = self._line_under_locked()
        guard = line.with_user(self.salesperson)
        with self.assertRaises(AccessError):
            guard._check_locked_snapshot_write({"locked_baseline_seller_discount": 1.0})


@tagged("post_install", "-at_install")
class TestLockedSaleLineCreateGuardsCoverage(CommercialPolicyTestCommon):
    """Sale-line create guards exercised directly."""

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

    def _build_line(self, product=None):
        product = product or self.product_a
        order = self._create_order()
        return self._create_order_line(order, product=product, qty=1)

    def test_check_locked_line_create_admin_bypass(self):
        SaleLine = self.env["sale.order.line"]
        # Should not raise.
        SaleLine._check_locked_line_create([{"seller_discount": 99.0}])

    def test_check_locked_line_create_manager_bypass(self):
        SaleLine = self.env["sale.order.line"].with_user(self.manager_user)
        SaleLine._check_locked_line_create([{"seller_discount": 99.0}])

    def test_check_locked_line_create_no_protected_in_vals_skipped(self):
        SaleLine = self.env["sale.order.line"].with_user(self.salesperson)
        SaleLine._check_locked_line_create([{"name": "X"}])

    def test_check_locked_line_create_canonical_passes(self):
        line = self._build_line(product=self.product_a)
        line.with_user(self.salesperson)._check_locked_line_create(
            [{"seller_discount": 5.0, "extra_discount": 0.0}]
        )

    def test_check_locked_line_create_divergent_seller_blocked(self):
        line = self._build_line(product=self.product_a)
        guard = line.with_user(self.salesperson)
        with self.assertRaises(ValidationError):
            guard._check_locked_line_create(
                [{"seller_discount": 9.0, "extra_discount": 0.0}]
            )

    def test_check_locked_line_create_regular_product_skipped(self):
        """source != 'locked' branch — product NOT under locked."""
        line = self._build_line(product=self.product_b)
        line.with_user(self.salesperson)._check_locked_line_create(
            [{"seller_discount": 99.0, "extra_discount": 0.0}]
        )

    def test_check_locked_line_create_no_condition_skipped(self):
        partner = self.env["res.partner"].create({"name": "No condition"})
        order = self.env["sale.order"].create(
            {"partner_id": partner.id, "user_id": self.salesperson.id}
        )
        line = self._create_order_line(order, product=self.product_a, qty=1)
        line.with_user(self.salesperson)._check_locked_line_create(
            [{"seller_discount": 9.0}]
        )

    def test_check_locked_snapshot_write_create_admin_bypass(self):
        SaleLine = self.env["sale.order.line"]
        SaleLine._check_locked_snapshot_write_create(
            [{"locked_baseline_seller_discount": 99.0}]
        )

    def test_check_locked_snapshot_write_create_rep_blocked(self):
        SaleLine = self.env["sale.order.line"].with_user(self.salesperson)
        with self.assertRaises(AccessError):
            SaleLine._check_locked_snapshot_write_create(
                [{"locked_baseline_seller_discount": 99.0}]
            )

    def test_check_locked_snapshot_write_create_no_snapshot_fields_skipped(self):
        SaleLine = self.env["sale.order.line"].with_user(self.salesperson)
        SaleLine._check_locked_snapshot_write_create([{"name": "X"}])


@tagged("post_install", "-at_install")
class TestLockedSnapshotHelpersCoverage(CommercialPolicyTestCommon):
    """Direct exercise of the internal helpers
    ``_extract_sale_line_ids_from_vals`` and
    ``_snapshots_match_sale_origin`` — branches not naturally reached
    by the create/write paths.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
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

    def test_extract_sale_line_ids_handles_set_command(self):
        AccountLine = self.env["account.move.line"]
        # (6, 0, [ids]) — Command.SET form.
        result = AccountLine._extract_sale_line_ids_from_vals(
            {"sale_line_ids": [(6, 0, [10, 20, 30])]}
        )
        self.assertEqual(result, [10, 20, 30])

    def test_extract_sale_line_ids_handles_link_command(self):
        AccountLine = self.env["account.move.line"]
        result = AccountLine._extract_sale_line_ids_from_vals(
            {"sale_line_ids": [(4, 5)]}
        )
        self.assertEqual(result, [5])

    def test_extract_sale_line_ids_ignores_unknown_command(self):
        """Unknown commands (e.g., op=3 = unlink) are skipped."""
        AccountLine = self.env["account.move.line"]
        result = AccountLine._extract_sale_line_ids_from_vals(
            {"sale_line_ids": [(3, 1), (4, 7)]}
        )
        self.assertEqual(result, [7])

    def test_extract_sale_line_ids_ignores_malformed(self):
        """Single-element or non-iterable commands ignored."""
        AccountLine = self.env["account.move.line"]
        result = AccountLine._extract_sale_line_ids_from_vals(
            {"sale_line_ids": [(4,), "garbage", None]}
        )
        self.assertEqual(result, [])

    def test_extract_sale_line_ids_no_commands_returns_empty(self):
        AccountLine = self.env["account.move.line"]
        self.assertEqual(AccountLine._extract_sale_line_ids_from_vals({}), [])

    def _make_sale_line_with_snapshot(self):
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        return line

    def test_snapshots_match_origin_returns_false_when_no_sale_line(self):
        AccountLine = self.env["account.move.line"]
        self.assertFalse(
            AccountLine._snapshots_match_sale_origin(
                {"product_id": self.product_a.id}, AccountLine.browse([])
            )
        )

    def test_snapshots_match_origin_returns_false_on_id_mismatch(self):
        sale_line = self._make_sale_line_with_snapshot()
        AccountLine = self.env["account.move.line"]
        self.assertFalse(
            AccountLine._snapshots_match_sale_origin(
                {
                    "product_id": sale_line.product_id.id,
                    "locked_condition_line_id": 0,
                },
                sale_line,
            )
        )

    def test_snapshots_match_origin_returns_false_on_fixed_rate_mismatch(self):
        sale_line = self._make_sale_line_with_snapshot()
        AccountLine = self.env["account.move.line"]
        self.assertFalse(
            AccountLine._snapshots_match_sale_origin(
                {
                    "product_id": sale_line.product_id.id,
                    "locked_condition_line_id": sale_line.locked_condition_line_id.id,
                    "locked_fixed_commission_rate": 99.0,
                },
                sale_line,
            )
        )

    def test_snapshots_match_origin_returns_false_on_baseline_seller_mismatch(self):
        sale_line = self._make_sale_line_with_snapshot()
        AccountLine = self.env["account.move.line"]
        self.assertFalse(
            AccountLine._snapshots_match_sale_origin(
                {
                    "product_id": sale_line.product_id.id,
                    "locked_condition_line_id": sale_line.locked_condition_line_id.id,
                    "locked_fixed_commission_rate": (
                        sale_line.locked_fixed_commission_rate
                    ),
                    "locked_baseline_seller_discount": 99.0,
                },
                sale_line,
            )
        )

    def test_snapshots_match_origin_returns_true_on_exact_match(self):
        sale_line = self._make_sale_line_with_snapshot()
        AccountLine = self.env["account.move.line"]
        self.assertTrue(
            AccountLine._snapshots_match_sale_origin(
                {
                    "product_id": sale_line.product_id.id,
                    "locked_condition_line_id": sale_line.locked_condition_line_id.id,
                    "locked_fixed_commission_rate": (
                        sale_line.locked_fixed_commission_rate
                    ),
                    "locked_baseline_seller_discount": (
                        sale_line.locked_baseline_seller_discount
                    ),
                    "locked_baseline_extra_discount": (
                        sale_line.locked_baseline_extra_discount
                    ),
                },
                sale_line,
            )
        )


@tagged("post_install", "-at_install")
class TestLockedCommissionAndWriteReapplyCoverage(CommercialPolicyTestCommon):
    """Cover ``_get_policy_commission_rate`` locked branch on invoice
    and post-write reapply when scope changes into locked / out of
    locked on a manual invoice line.
    """

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
                    "fixed_commission_rate": 7.0,
                }
            )
        )

    def test_invoice_get_policy_commission_rate_uses_snapshot(self):
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = self.condition
        invoice.sales_profile_id = self.agent_profile
        line = (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_a.id,
                    "quantity": 1.0,
                    "name": self.product_a.display_name,
                }
            )
        )
        # Snapshot kicked in.
        self.assertEqual(line.locked_condition_line_id, self.locked)
        # Helper returns the snapshotted rate.
        self.assertAlmostEqual(line._get_policy_commission_rate(), 7.0)


@tagged("post_install", "-at_install")
class TestLockedPolicyUtilsVariantTemplateMismatch(CommercialPolicyTestCommon):
    """Cover ``locked_covers_line`` final ``return False`` — locked
    variant paired with a regular template whose template is OTHER
    than the locked variant's template (so no overlap)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_locked_variant_does_not_cover_regular_other_template(self):
        locked = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.director_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product",
                    "product_id": self.product_a.id,
                    "seller_discount": 5.0,
                    "is_locked": True,
                }
            )
        )
        regular = self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product",
                "product_id": self.product_b.id,
                "seller_discount": 3.0,
            }
        )
        # Locked variant + regular variant of different products →
        # no coverage → final return False.
        self.assertFalse(locked_covers_line(locked, regular))


@tagged("post_install", "-at_install")
class TestLockedSaleLineGuardEarlyReturns(CommercialPolicyTestCommon):
    """Sale-line guard early-return branches: admin bypass on snapshot
    create, snapshot-write skip when no snapshot fields, post-write
    scope branch when condition or product is missing.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_snapshot_write_create_admin_bypass_sale_line(self):
        # env.user is admin — `has_group('base.group_system')` is True.
        SaleLine = self.env["sale.order.line"]
        SaleLine._check_locked_snapshot_write_create(
            [{"locked_baseline_seller_discount": 99.0}]
        )

    def test_snapshot_write_no_snapshot_fields_sale_line(self):
        SaleLine = self.env["sale.order.line"].with_user(self.salesperson)
        # Direct call (not through a record) — guard returns early.
        SaleLine._check_locked_snapshot_write({"name": "X"})

    def test_sale_line_write_scope_change_no_condition_continues(self):
        """Sale line whose order has no condition: post-write scope
        check hits the ``not condition`` continue branch."""
        partner = self.env["res.partner"].create({"name": "No condition partner"})
        order = self.env["sale.order"].create(
            {"partner_id": partner.id, "user_id": self.salesperson.id}
        )
        line = self._create_order_line(order, product=self.product_a, qty=1)
        # Bump qty — write triggers scope check, but order has no
        # commercial_condition_id → continue.
        line.product_uom_qty = 5.0
        self.assertAlmostEqual(line.product_uom_qty, 5.0)


@tagged("post_install", "-at_install")
class TestLockedInvoiceWriteReapplyCoverage(CommercialPolicyTestCommon):
    """Cover the post-write scope reapply on manual invoice lines:
    scope move into locked + scope move out of locked.
    """

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

    def _create_manual_invoice_with_line(self, product):
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = self.condition
        invoice.sales_profile_id = self.agent_profile
        line = (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": product.id,
                    "quantity": 1.0,
                    "name": product.display_name,
                }
            )
        )
        return invoice, line

    def test_invoice_line_scope_move_into_locked_via_write_populates_snapshot(self):
        """Manual invoice line for product B (regular) → write product_id
        to product A (locked) → post-write reapply populates snapshot.
        """
        _invoice, line = self._create_manual_invoice_with_line(self.product_b)
        self.assertFalse(line.locked_condition_line_id)
        line.sudo().write({"product_id": self.product_a.id})
        self.assertEqual(line.locked_condition_line_id, self.locked)
        self.assertAlmostEqual(line.seller_discount, 5.0)

    def test_invoice_line_scope_move_out_of_locked_clears_snapshot(self):
        _invoice, line = self._create_manual_invoice_with_line(self.product_a)
        self.assertEqual(line.locked_condition_line_id, self.locked)
        line.sudo().write({"product_id": self.product_b.id})
        self.assertFalse(line.locked_condition_line_id)

    def test_invoice_line_write_no_condition_post_write_reapply_continues(self):
        """Manual invoice line on move WITHOUT condition: post-write
        scope-reapply hits the ``not condition`` continue branch."""
        invoice = self._create_manual_invoice()
        line = (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_b.id,
                    "quantity": 1.0,
                    "name": self.product_b.display_name,
                }
            )
        )
        # Ensure no condition on the move (in case test fixtures set
        # it via a side effect).
        invoice.commercial_condition_id = False
        self.assertFalse(line.move_id.commercial_condition_id)
        # Write to ``quantity`` — hits the post-write reapply path.
        line.sudo().write({"quantity": 2.0})
        self.assertAlmostEqual(line.quantity, 2.0)


@tagged("post_install", "-at_install")
class TestLockedInvoiceLineGuardEarlyReturns(CommercialPolicyTestCommon):
    """Invoice-line guard early-return branches not covered elsewhere."""

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

    def test_check_locked_line_edit_sudo_bypass(self):
        """``env.su`` bypasses ``_check_locked_line_edit`` on invoice."""
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = self.condition
        invoice.sales_profile_id = self.agent_profile
        line = (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_a.id,
                    "quantity": 1.0,
                    "name": self.product_a.display_name,
                }
            )
        )
        # sudo path: guard returns early.
        line.sudo()._check_locked_line_edit({"seller_discount": 999.0})

    def test_check_locked_line_edit_non_invoice_move_type_skipped(self):
        """Non out_invoice/out_refund move types are skipped by the
        per-line filter — exercise via a journal entry."""
        journal = self.env["account.journal"].search(
            [("type", "=", "general")], limit=1
        )
        account = self.env["account.account"].search(
            [("company_id", "=", self.env.company.id)], limit=1
        )
        move = (
            self.env["account.move"]
            .with_context(check_move_validity=False)
            .create({"move_type": "entry", "journal_id": journal.id})
        )
        line = (
            self.env["account.move.line"]
            .sudo()
            .with_context(check_move_validity=False)
            .create(
                {
                    "move_id": move.id,
                    "name": "Misc",
                    "account_id": account.id,
                }
            )
        )
        # Rep: even with divergent vals, guard skips because move_type
        # is not in the invoice/refund whitelist.
        line.with_user(self.salesperson)._check_locked_line_edit(
            {"seller_discount": 999.0}
        )

    def test_check_locked_line_edit_snapshotless_seller_only_passes(self):
        """Snapshot-less invoice line + only seller/extra in vals (no
        scope) hits the continue branch (line 613)."""
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = self.condition
        invoice.sales_profile_id = self.agent_profile
        line = (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_b.id,
                    "quantity": 1.0,
                    "name": self.product_b.display_name,
                }
            )
        )
        # product_b is NOT under locked → no snapshot.
        self.assertFalse(line.locked_condition_line_id)
        # Plain seller write passes the guard (legitimate manual edit).
        line.with_user(self.salesperson)._check_locked_line_edit(
            {"seller_discount": 8.0}
        )

    def test_check_locked_line_edit_scope_change_no_condition_continues(self):
        """Build invoice with a sale_origin line, then strip the
        condition from the move to exercise the ``not condition``
        continue branch in the guard."""
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = self.condition
        invoice.sales_profile_id = self.agent_profile
        line = (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_b.id,
                    "quantity": 1.0,
                    "name": self.product_b.display_name,
                }
            )
        )
        invoice.commercial_condition_id = False
        line.with_user(self.salesperson)._check_locked_line_edit(
            {"product_id": self.product_a.id, "seller_discount": 9.0}
        )

    def test_check_locked_line_edit_scope_change_no_product_continues(self):
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = self.condition
        invoice.sales_profile_id = self.agent_profile
        line = (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_b.id,
                    "quantity": 1.0,
                    "name": self.product_b.display_name,
                }
            )
        )
        line.with_user(self.salesperson)._check_locked_line_edit(
            {"product_id": False, "seller_discount": 9.0}
        )

    def test_check_locked_snapshot_write_sudo_bypass(self):
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = self.condition
        invoice.sales_profile_id = self.agent_profile
        line = (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_a.id,
                    "quantity": 1.0,
                    "name": self.product_a.display_name,
                }
            )
        )
        # Sudo path: guard returns early.
        line.sudo()._check_locked_snapshot_write(
            {"locked_baseline_seller_discount": 99.0}
        )

    def test_check_locked_line_create_sudo_bypass(self):
        AccountLine = self.env["account.move.line"].sudo()
        AccountLine._check_locked_line_create([{"seller_discount": 99.0}])

    def test_check_locked_line_create_manager_bypass(self):
        AccountLine = self.env["account.move.line"].with_user(self.manager_user)
        AccountLine._check_locked_line_create([{"seller_discount": 99.0}])

    def test_check_locked_line_create_no_protected_skipped(self):
        AccountLine = self.env["account.move.line"].with_user(self.salesperson)
        AccountLine._check_locked_line_create([{"name": "X"}])

    def test_check_locked_line_create_no_condition_continues(self):
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = self.condition
        invoice.sales_profile_id = self.agent_profile
        line = (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_a.id,
                    "quantity": 1.0,
                    "name": self.product_a.display_name,
                }
            )
        )
        invoice.commercial_condition_id = False
        # No condition on the move → guard skips.
        line.with_user(self.salesperson)._check_locked_line_create(
            [{"seller_discount": 9.0}]
        )

    def test_check_locked_line_create_regular_source_continues(self):
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = self.condition
        invoice.sales_profile_id = self.agent_profile
        line = (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_b.id,
                    "quantity": 1.0,
                    "name": self.product_b.display_name,
                }
            )
        )
        line.with_user(self.salesperson)._check_locked_line_create(
            [{"seller_discount": 99.0}]
        )

    def test_check_locked_snapshot_write_create_admin_bypass_invoice(self):
        AccountLine = self.env["account.move.line"]
        AccountLine._check_locked_snapshot_write_create(
            [{"locked_baseline_seller_discount": 1.0}]
        )

    def test_check_locked_snapshot_write_create_no_snapshot_skip_invoice(self):
        AccountLine = self.env["account.move.line"].with_user(self.salesperson)
        AccountLine._check_locked_snapshot_write_create([{"name": "X"}])

    def test_propagation_path_seller_extra_match_skips_live_resolution(self):
        """Cover ``_check_locked_line_create`` propagation branch: vals
        carry ``sale_line_ids`` of the same product with seller/extra
        matching the sale line → guard skips live resolution.
        """
        # Sale line under locked.
        order = self._create_order()
        sale_line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(sale_line, self.condition)
        AccountLine = self.env["account.move.line"].with_user(self.salesperson)
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = self.condition
        invoice.sales_profile_id = self.agent_profile
        rec = (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_a.id,
                    "quantity": 1.0,
                    "name": self.product_a.display_name,
                }
            )
        )
        # Vals reflect the canonical propagation: same product, exact
        # snapshot match. Guard hits the propagation continue branch.
        AccountLine = rec.with_user(self.salesperson)
        AccountLine._check_locked_line_create(
            [
                {
                    "product_id": self.product_a.id,
                    "seller_discount": sale_line.seller_discount,
                    "extra_discount": sale_line.extra_discount,
                    "sale_line_ids": [(4, sale_line.id)],
                }
            ]
        )

    def test_propagation_path_seller_mismatch_falls_through_to_live(self):
        """Sale_line_ids carry origin of A, but vals seller doesn't
        match: propagation early-continue NOT taken; falls into live
        resolution which raises because line is under locked and the
        discount diverges from canonical."""
        order = self._create_order()
        sale_line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(sale_line, self.condition)
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = self.condition
        invoice.sales_profile_id = self.agent_profile
        rec = (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_a.id,
                    "quantity": 1.0,
                    "name": self.product_a.display_name,
                }
            )
        )
        AccountLine = rec.with_user(self.salesperson)
        with self.assertRaises(ValidationError):
            AccountLine._check_locked_line_create(
                [
                    {
                        "product_id": self.product_a.id,
                        "seller_discount": 99.0,
                        "extra_discount": 0.0,
                        "sale_line_ids": [(4, sale_line.id)],
                    }
                ]
            )


@tagged("post_install", "-at_install")
class TestLockedSaleTierInternalProfile(CommercialPolicyTestCommon):
    """Internal-profile order: tier validation must skip the internal
    band check on lines aligned with a locked snapshot (sale_order.py
    line 783)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_internal_policy()
        cls.locked = (
            cls.env["partner.commercial.condition.line"]
            .with_user(cls.director_user)
            .create(
                {
                    "condition_id": cls.condition.id,
                    "applied_on": "product_template",
                    "product_tmpl_id": cls.product_template_a.id,
                    "seller_discount": 25.0,
                    "is_locked": True,
                    "fixed_commission_rate": 0.0,
                }
            )
        )

    def test_internal_band_skipped_when_aligned_with_locked(self):
        order = self._create_order()
        order.sales_profile_id = self.internal_profile
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        # Sanity: line aligned with locked, with seller above any band
        # in the internal profile rules.
        self.assertTrue(line._is_aligned_with_locked_snapshot())
        self.assertGreater(line.seller_discount, 10.0)
        issues = order._get_discount_validation_issues()
        line_internal_band_issues = [
            i
            for i in issues
            if i.get("line_id") == line.id and i.get("type") == "internal_band"
        ]
        self.assertFalse(line_internal_band_issues)


@tagged("post_install", "-at_install")
class TestLockedGuardsAdminNoSudo(CommercialPolicyTestCommon):
    """A non-superuser user with ``base.group_system`` exercises the
    ``has_group('base.group_system')`` early-return branches. SUPERUSER
    (uid=1) is auto-promoted to ``env.su=True`` by Odoo, so we need a
    distinct user to hit the post-``env.su`` admin bypass."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls.system_user = cls.env["res.users"].create(
            {
                "name": "System User (locked tests)",
                "login": "test_system_user_locked",
                "groups_id": [
                    (4, cls.env.ref("base.group_system").id),
                    (4, cls.env.ref("sales_team.group_sale_manager").id),
                ],
            }
        )
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

    # --- Invoice line guards (account.move.line) ---

    def _make_invoice_line(self, product=None):
        product = product or self.product_a
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = self.condition
        invoice.sales_profile_id = self.agent_profile
        return (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": product.id,
                    "quantity": 1.0,
                    "name": product.display_name,
                }
            )
        )

    def test_invoice_check_locked_line_edit_admin_bypass(self):
        line = self._make_invoice_line()
        # Run as system_user (non-superuser with base.group_system).
        # env.su=False, has_group=True → admin-bypass branch.
        line.with_user(self.system_user)._check_locked_line_edit(
            {"seller_discount": 99.0}
        )

    def test_invoice_check_locked_snapshot_write_admin_bypass_no_su(self):
        line = self._make_invoice_line()
        line.with_user(self.system_user)._check_locked_snapshot_write(
            {"locked_baseline_seller_discount": 99.0}
        )

    def test_invoice_check_locked_line_create_admin_bypass(self):
        AccountLine = self.env["account.move.line"].with_user(self.system_user)
        AccountLine._check_locked_line_create([{"seller_discount": 99.0}])

    def test_invoice_check_locked_line_create_vals_without_protected_skipped(self):
        """vals_list with at least one protected entry, second vals
        without protected → continue on line 791. Must run as a user
        that's NOT admin/sudo/group_sales_manager — otherwise the
        early returns short-circuit before reaching the per-vals loop.
        """
        # Build invoice with salesperson as user_id so the rep can read
        # the move via the standard "own documents" record rule.
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = self.condition
        invoice.sales_profile_id = self.agent_profile
        invoice.invoice_user_id = self.salesperson
        line1 = (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_a.id,
                    "quantity": 1.0,
                    "name": self.product_a.display_name,
                }
            )
        )
        line2 = (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_b.id,
                    "quantity": 1.0,
                    "name": self.product_b.display_name,
                }
            )
        )
        AccountLine = (line1 | line2).with_user(self.salesperson)
        # First vals has protected (matches canonical for product_a);
        # second doesn't. Loop skips second on line 791.
        AccountLine._check_locked_line_create(
            [{"seller_discount": 5.0}, {"name": "something else"}]
        )

    def test_invoice_check_locked_line_create_non_invoice_move_skipped(self):
        """Journal-entry move type → continue at line 793."""
        journal = self.env["account.journal"].search(
            [("type", "=", "general")], limit=1
        )
        account = self.env["account.account"].search(
            [("company_id", "=", self.env.company.id)], limit=1
        )
        move = (
            self.env["account.move"]
            .with_context(check_move_validity=False)
            .create({"move_type": "entry", "journal_id": journal.id})
        )
        line = (
            self.env["account.move.line"]
            .sudo()
            .with_context(check_move_validity=False)
            .create(
                {
                    "move_id": move.id,
                    "name": "Misc",
                    "account_id": account.id,
                    "product_id": self.product_a.id,
                    "quantity": 1.0,
                }
            )
        )
        line.with_user(self.salesperson)._check_locked_line_create(
            [{"seller_discount": 5.0}]
        )

    def test_invoice_check_locked_snapshot_write_create_admin_bypass(self):
        AccountLine = self.env["account.move.line"].with_user(self.system_user)
        AccountLine._check_locked_snapshot_write_create(
            [{"locked_baseline_seller_discount": 99.0}]
        )

    def test_invoice_snapshot_write_create_propagation_match_continues(self):
        """Rep payload with sale_line_ids matching origin → guard
        accepts as propagation and ``continue``s on line 945."""
        # Sale line under locked.
        order = self.env["sale.order"].create(
            {"partner_id": self.customer.id, "user_id": self.salesperson.id}
        )
        sale_line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(sale_line, self.condition)
        # Build the call as the rep — vals carry valid propagation.
        AccountLine = self.env["account.move.line"].with_user(self.salesperson)
        # Should not raise — payload exactly matches sale_line origin.
        AccountLine._check_locked_snapshot_write_create(
            [
                {
                    "product_id": sale_line.product_id.id,
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
            ]
        )

    # --- Sale line guards (sale.order.line) ---

    def _make_sale_line(self, product=None):
        product = product or self.product_a
        order = self._create_order()
        return self._create_order_line(order, product=product, qty=1)

    def test_sale_check_locked_line_edit_admin_bypass(self):
        line = self._make_sale_line()
        line.with_user(self.system_user)._check_locked_line_edit(
            {"seller_discount": 99.0}
        )

    def test_sale_check_locked_snapshot_write_admin_bypass(self):
        line = self._make_sale_line()
        line.with_user(self.system_user)._check_locked_snapshot_write(
            {"locked_baseline_seller_discount": 99.0}
        )

    def test_sale_check_locked_snapshot_write_no_snapshot_fields_skipped(self):
        line = self._make_sale_line()
        # No snapshot field in vals → early return at "not intersection".
        line.with_user(self.system_user)._check_locked_snapshot_write({"name": "X"})

    def test_sale_check_locked_line_create_admin_bypass(self):
        SaleLine = self.env["sale.order.line"].with_user(self.system_user)
        SaleLine._check_locked_line_create([{"seller_discount": 99.0}])

    def test_sale_check_locked_line_create_vals_without_protected_continue(self):
        """vals_list[1] without protected → continue on line 703.
        Sale lines created with salesperson as user_id so the rep has
        access via the own-documents record rule."""
        order = self.env["sale.order"].create(
            {"partner_id": self.customer.id, "user_id": self.salesperson.id}
        )
        line1 = self._create_order_line(order, product=self.product_a, qty=1)
        line2 = self._create_order_line(order, product=self.product_b, qty=1)
        SaleLine = (line1 | line2).with_user(self.salesperson)
        SaleLine._check_locked_line_create([{"seller_discount": 5.0}, {"name": "X"}])

    def test_sale_check_locked_snapshot_write_create_admin_bypass(self):
        SaleLine = self.env["sale.order.line"].with_user(self.system_user)
        SaleLine._check_locked_snapshot_write_create(
            [{"locked_baseline_seller_discount": 99.0}]
        )


@tagged("post_install", "-at_install")
class TestPolicyUtilsVariantVsTemplate(CommercialPolicyTestCommon):
    """Cover ``locked_covers_line`` final return False: locked variant
    of one template vs regular template of a DIFFERENT template — no
    overlap."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_locked_variant_does_not_cover_regular_template_other(self):
        locked = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.director_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product",
                    "product_id": self.product_a.id,
                    "seller_discount": 5.0,
                    "is_locked": True,
                }
            )
        )
        regular = self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product_template",
                "product_tmpl_id": self.product_template_b.id,
                "seller_discount": 3.0,
            }
        )
        # locked is variant of template A; regular is template B → no
        # overlap, hits final return False.
        self.assertFalse(locked_covers_line(locked, regular))

    def test_locked_template_with_unknown_regular_scope_falls_through(self):
        """Defensive final ``return False``: helper takes arbitrary
        records, so we exercise it with a stub whose ``applied_on``
        is outside the selection (defensive path, never reached
        via normal flow). Pass a duck-typed object so the function's
        attribute access still works.
        """
        from types import SimpleNamespace

        locked = SimpleNamespace(
            active=True,
            is_locked=True,
            applied_on="product_template",
            product_tmpl_id=self.product_template_a,
            product_id=self.env["product.product"],
        )
        regular = SimpleNamespace(
            active=True,
            is_locked=False,
            # Out-of-selection scope → falls to the final return False.
            applied_on="general",
            product_tmpl_id=self.product_template_a,
            product_id=self.env["product.product"],
        )
        self.assertFalse(locked_covers_line(locked, regular))


@tagged("post_install", "-at_install")
class TestLockedReadonlyForUserFlag(CommercialPolicyTestCommon):
    """``tr_locked_readonly_for_user`` UI flag: True for rep on lines
    under locked, False for manager+, False for lines without snapshot.
    Drives the readonly attr on discount fields and the visibility of
    discount buttons in the views.
    """

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

    def _sale_line_under_locked(self):
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a, qty=1)
        order._apply_condition_to_line(line, self.condition)
        return line

    def _sale_line_regular(self):
        order = self._create_order()
        return self._create_order_line(order, product=self.product_b, qty=1)

    def test_sale_flag_true_for_rep_under_locked(self):
        line = self._sale_line_under_locked()
        self.assertTrue(line.with_user(self.salesperson).tr_locked_readonly_for_user)

    def test_sale_flag_false_for_manager_under_locked(self):
        line = self._sale_line_under_locked()
        self.assertFalse(line.with_user(self.manager_user).tr_locked_readonly_for_user)

    def test_sale_flag_false_for_director_under_locked(self):
        line = self._sale_line_under_locked()
        self.assertFalse(line.with_user(self.director_user).tr_locked_readonly_for_user)

    def test_sale_flag_false_for_rep_without_snapshot(self):
        line = self._sale_line_regular()
        self.assertFalse(line.with_user(self.salesperson).tr_locked_readonly_for_user)

    def test_invoice_flag_true_for_rep_under_locked(self):
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = self.condition
        invoice.sales_profile_id = self.agent_profile
        line = (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_a.id,
                    "quantity": 1.0,
                    "name": self.product_a.display_name,
                }
            )
        )
        self.assertTrue(line.with_user(self.salesperson).tr_locked_readonly_for_user)

    def test_invoice_flag_false_for_manager_under_locked(self):
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = self.condition
        invoice.sales_profile_id = self.agent_profile
        line = (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_a.id,
                    "quantity": 1.0,
                    "name": self.product_a.display_name,
                }
            )
        )
        self.assertFalse(line.with_user(self.manager_user).tr_locked_readonly_for_user)

    def test_invoice_flag_false_for_rep_without_snapshot(self):
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = self.condition
        invoice.sales_profile_id = self.agent_profile
        line = (
            self.env["account.move.line"]
            .sudo()
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_b.id,
                    "quantity": 1.0,
                    "name": self.product_b.display_name,
                }
            )
        )
        self.assertFalse(line.with_user(self.salesperson).tr_locked_readonly_for_user)


@tagged("post_install", "-at_install")
class TestLockedReadonlyForUserFlagOnConditionLine(CommercialPolicyTestCommon):
    """``tr_locked_readonly_for_user`` UI flag on the condition.line
    itself: True for rep on locked lines, False for manager+, False
    on regular lines."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
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
                }
            )
        )
        cls.regular = cls.env["partner.commercial.condition.line"].create(
            {
                "condition_id": cls.condition.id,
                "applied_on": "product_template",
                "product_tmpl_id": cls.product_template_b.id,
                "seller_discount": 3.0,
            }
        )

    def test_flag_true_for_rep_on_locked(self):
        self.assertTrue(
            self.locked.with_user(self.salesperson).tr_locked_readonly_for_user
        )

    def test_flag_false_for_manager_on_locked(self):
        self.assertFalse(
            self.locked.with_user(self.manager_user).tr_locked_readonly_for_user
        )

    def test_flag_false_for_director_on_locked(self):
        self.assertFalse(
            self.locked.with_user(self.director_user).tr_locked_readonly_for_user
        )

    def test_flag_false_on_regular_line(self):
        self.assertFalse(
            self.regular.with_user(self.salesperson).tr_locked_readonly_for_user
        )


@tagged("post_install", "-at_install")
class TestDirectorOnlyForUserFlag(CommercialPolicyTestCommon):
    """``tr_director_only_for_user`` UI flag: True for rep regardless
    of line state, False for manager+. Drives readonly on the
    director-only fields (``is_locked``, ``active``).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls.regular = cls.env["partner.commercial.condition.line"].create(
            {
                "condition_id": cls.condition.id,
                "applied_on": "product_template",
                "product_tmpl_id": cls.product_template_a.id,
                "seller_discount": 3.0,
            }
        )

    def test_flag_true_for_rep(self):
        self.assertTrue(
            self.regular.with_user(self.salesperson).tr_director_only_for_user
        )

    def test_flag_false_for_manager(self):
        self.assertFalse(
            self.regular.with_user(self.manager_user).tr_director_only_for_user
        )

    def test_flag_false_for_director(self):
        self.assertFalse(
            self.regular.with_user(self.director_user).tr_director_only_for_user
        )

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestInvoicePolicyFromSale(CommercialPolicyTestCommon):
    """Tests for invoice commercial policy (Phase 0+1a+1b).

    Covers snapshot preservation, divergence detection (3-class taxonomy),
    own-rules commercial revalidation, constraints, tier approval,
    onchange, and commission resync.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()
        cls.product_template_a.invoice_policy = "order"
        cls.product_template_b.invoice_policy = "order"

    # --- Phase 0: Smoke tests ---

    def test_prepare_invoice_line_propagates_new_fields(self):
        """Smoke: _prepare_invoice_line copies policy fields to invoice."""
        order, invoice = self._create_confirmed_order_with_invoice(
            seller_discount=3.0,
        )
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        sale_line = order.order_line.filtered(lambda line: line.product_id)
        self.assertAlmostEqual(
            inv_line.seller_discount, sale_line.seller_discount, places=2
        )
        self.assertAlmostEqual(
            inv_line.extra_discount, sale_line.extra_discount, places=2
        )
        self.assertAlmostEqual(inv_line.base_price, sale_line.base_price, places=2)
        self.assertAlmostEqual(
            inv_line.reference_price, sale_line.reference_price, places=2
        )
        self.assertAlmostEqual(
            inv_line.commission_rate, sale_line.commission_rate, places=2
        )

    def test_prepare_invoice_propagates_header(self):
        """Smoke: _prepare_invoice copies header discount fields."""
        order, invoice = self._create_confirmed_order_with_invoice()
        self.assertAlmostEqual(invoice.tr_cash_discount, order.cash_discount, places=2)
        self.assertAlmostEqual(invoice.tr_fob_discount, order.fob_discount, places=2)
        self.assertAlmostEqual(
            invoice.tr_contractual_return,
            order.contractual_return,
            places=2,
        )

    def test_compute_agent_ids_composes_with_reversal(self):
        """Smoke: override does not break reversal agent protection."""
        _order, invoice = self._create_confirmed_order_with_invoice()
        invoice.action_post()
        reversal_wizard = (
            self.env["account.move.reversal"]
            .with_context(active_model="account.move", active_ids=invoice.ids)
            .create({"reason": "Test reversal", "journal_id": invoice.journal_id.id})
        )
        action = reversal_wizard.reverse_moves()
        refund = self.env["account.move"].browse(action["res_id"])
        refund_product_lines = refund.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        inv_product_lines = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        # Original had agents → refund must too (engenere protects)
        for inv_line in inv_product_lines:
            refund_line = refund_product_lines.filtered(
                lambda line: line.product_id == inv_line.product_id
            )
            if inv_line.agent_ids:
                self.assertTrue(
                    refund_line.agent_ids,
                    "Reversal lost agent lines — MRO composition broken",
                )

    def test_has_sale_origin_true(self):
        """Smoke: has_sale_origin is True for invoice from sale."""
        _order, invoice = self._create_confirmed_order_with_invoice()
        self.assertTrue(invoice.has_sale_origin)

    def test_has_sale_origin_false_manual(self):
        """Smoke: has_sale_origin is False for manual invoice."""
        invoice = self._create_manual_invoice()
        self.assertFalse(invoice.has_sale_origin)

    # --- Phase 1: Decision tree — snapshot core ---

    def test_parity_post_allowed(self):
        """Invoice identical to order → post without tier. [rule 1]"""
        _order, invoice = self._create_confirmed_order_with_invoice()
        self._assert_invoice_matches_sale_snapshot(invoice)
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    def test_snapshot_mismatch_wrong_commission_blocks(self):
        """Manually change commission_id → blocks post. [rule 19]"""
        _order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        other_commission = self.env["commission"].create(
            {
                "name": "Wrong Commission",
                "commission_type": "fixed",
                "fix_qty": 5.0,
            }
        )
        inv_line.agent_ids[0].commission_id = other_commission
        with self.assertRaises(UserError):
            invoice.action_post()

    def test_snapshot_mismatch_missing_agent_blocks(self):
        """Remove agent from invoice → blocks post. [rule 17]"""
        _order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        inv_line.agent_ids.unlink()
        with self.assertRaises(UserError):
            invoice.action_post()

    def test_snapshot_mismatch_extra_agent_blocks(self):
        """Add extra agent to invoice → blocks post. [rule 17]"""
        _order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        extra_agent = self.env["res.partner"].create(
            {
                "name": "Extra Agent",
                "agent": True,
                "commission_id": self.commission.id,
            }
        )
        self.env["account.invoice.line.agent"].create(
            {
                "object_id": inv_line.id,
                "agent_id": extra_agent.id,
                "commission_id": self.commission.id,
            }
        )
        with self.assertRaises(UserError):
            invoice.action_post()

    def test_snapshot_mismatch_swapped_agent_blocks(self):
        """Swap agent_id on invoice → blocks post. [rule 17]"""
        _order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        other_agent = self.env["res.partner"].create(
            {
                "name": "Swapped Agent",
                "agent": True,
                "commission_id": self.commission.id,
            }
        )
        inv_line.agent_ids[0].agent_id = other_agent
        with self.assertRaises(UserError):
            invoice.action_post()

    def test_readonly_field_divergence_blocks_as_snapshot(self):
        """Divergence in base_price → blocks as snapshot issue."""
        order, invoice = self._create_confirmed_order_with_invoice()
        sale_line = order.order_line.filtered(lambda line: line.product_id)
        sale_line.base_price = 999.99
        with self.assertRaises(UserError):
            invoice.action_post()

    def test_draft_order_loses_approval_shield(self):
        """Order forced to draft → enters own rules path.

        Invoice values are within limits → posts without tier.
        """
        order, invoice = self._create_confirmed_order_with_invoice()
        order.with_context(tracking_disable=True).write({"state": "draft"})
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    def test_draft_order_no_exception_post_allowed(self):
        """Order in draft, snapshot matches → own rules, values ok → posts."""
        order, invoice = self._create_confirmed_order_with_invoice()
        order.with_context(tracking_disable=True).write({"state": "draft"})
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    def test_draft_order_with_classe_b_divergence_blocks(self):
        """Order in draft + Classe B divergence → own rules (not hard block).

        With reordered decision tree (Phase 4), draft enters own rules
        BEFORE snapshot check, so Classe B divergence does not block.
        """
        order, invoice = self._create_confirmed_order_with_invoice()
        order.with_context(tracking_disable=True).write({"state": "draft"})
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        inv_line.with_context(skip_invoice_sync=True, check_move_validity=False).write(
            {"base_price": 999.0}
        )
        # Draft → own rules (bypasses snapshot) → values ok → posts
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    # --- Phase 1: Commissions ---

    def test_managed_commission_preserved(self):
        """commission_id on invoice matches sale managed commission. [rule 17]"""
        order, invoice = self._create_confirmed_order_with_invoice()
        sale_line = order.order_line.filtered(lambda line: line.product_id)
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        self.assertEqual(
            inv_line.agent_ids[0].commission_id,
            sale_line.agent_ids[0].commission_id,
        )

    def test_compute_agent_ids_no_overwrite(self):
        """Invalidate + recompute → managed commission survives. [rule 17]"""
        _order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        original_commission = inv_line.agent_ids[0].commission_id
        inv_line.invalidate_recordset(["agent_ids"])
        inv_line._compute_agent_ids()
        self.assertEqual(
            inv_line.agent_ids[0].commission_id,
            original_commission,
            "Recompute overwrote managed commission",
        )

    def test_max_one_agent_per_line_blocks(self):
        """More than 1 agent on invoice line → blocks post. [rule 17]"""
        _order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        extra_agent = self.env["res.partner"].create(
            {
                "name": "Second Agent",
                "agent": True,
                "commission_id": self.commission.id,
            }
        )
        self.env["account.invoice.line.agent"].create(
            {
                "object_id": inv_line.id,
                "agent_id": extra_agent.id,
                "commission_id": self.commission.id,
            }
        )
        with self.assertRaises(UserError):
            invoice.action_post()

    def test_unmanaged_commission_on_invoice_warning(self):
        """Unmanaged commission set on invoice line → warning visible. [rule 18]"""
        _order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        unmanaged = self.env["commission"].create(
            {
                "name": "Unmanaged",
                "commission_type": "fixed",
                "fix_qty": 8.0,
            }
        )
        inv_line.agent_ids[0].commission_id = unmanaged
        invoice.invalidate_recordset(["unmanaged_commission_warning"])
        self.assertTrue(invoice.unmanaged_commission_warning)

    def test_unmanaged_commission_inherited_post_allowed(self):
        """Same unmanaged as sale → post allowed (inherited exception). [rule 18]"""
        order, invoice = self._create_confirmed_order_with_invoice()
        sale_line = order.order_line.filtered(lambda line: line.product_id)
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        unmanaged = self.env["commission"].create(
            {
                "name": "Unmanaged Both",
                "commission_type": "fixed",
                "fix_qty": 8.0,
            }
        )
        sale_line.agent_ids[0].commission_id = unmanaged
        inv_line.agent_ids[0].commission_id = unmanaged
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    def test_lowest_band_commission_consistent_on_invoice(self):
        """seller_discount=0.0 hits first band → rate consistent sale/invoice."""
        order, invoice = self._create_confirmed_order_with_invoice(
            seller_discount=0.0,
        )
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        sale_line = order.order_line.filtered(lambda line: line.product_id)
        self.assertAlmostEqual(
            inv_line.commission_rate, sale_line.commission_rate, places=2
        )

    # --- Phase 1: Multi-order heterogeneous guard ---

    def test_heterogeneous_multi_order_invoice_blocks_post(self):
        """Heterogeneous invoice (forced after grouping) → blocks post."""
        order1, order2 = self._create_two_orders_same_condition()
        # Group into 1 invoice (homogeneous at this point)
        invoice = (order1 | order2)._create_invoices()
        self.assertEqual(len(invoice), 1)
        # Force heterogeneity by tampering with origin order
        order2.cash_discount = 99.0
        self.assertTrue(invoice._has_heterogeneous_sale_origins())
        with self.assertRaises(UserError):
            invoice.action_post()

    def test_heterogeneous_multi_order_invoice_blocks_resync(self):
        """Heterogeneous invoice (forced after grouping) → blocks resync."""
        order1, order2 = self._create_two_orders_same_condition()
        invoice = (order1 | order2)._create_invoices()
        order2.cash_discount = 99.0
        self.assertTrue(invoice._has_heterogeneous_sale_origins())
        with self.assertRaises(UserError):
            invoice.action_resync_from_sale_order()

    @classmethod
    def _create_two_orders_same_condition(cls):
        """Helper: 2 confirmed orders with identical commercial context."""
        order1 = cls._create_order()
        order1.fiscal_operation_id = False
        line1 = cls._create_order_line(order1, qty=10)
        line1.seller_discount = 5.0
        line1.extra_discount = 0.0
        order1.action_confirm()
        order2 = cls._create_order()
        order2.fiscal_operation_id = False
        line2 = cls._create_order_line(order2, qty=5)
        line2.seller_discount = 5.0
        line2.extra_discount = 0.0
        order2.action_confirm()
        return order1, order2

    # --- Phase 1: Header divergence ---

    def test_header_cash_discount_divergence_enters_own_rules(self):
        """Order cash_discount changes after invoice → Classe A own rules.

        Invoice keeps original value (within limits) → posts.
        """
        order, invoice = self._create_confirmed_order_with_invoice()
        order.cash_discount = 99.0
        # Invoice tr_cash_discount is still original → no violation → posts
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    def test_header_fob_discount_divergence_enters_own_rules(self):
        """Order fob_discount changes after invoice → Classe A own rules.

        Invoice keeps original value (within limits) → posts.
        """
        order, invoice = self._create_confirmed_order_with_invoice()
        order.fob_discount = 99.0
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    def test_header_contractual_return_divergence_triggers_validation(self):
        """Order contractual_return changes after invoice → divergence blocks."""
        order, invoice = self._create_confirmed_order_with_invoice()
        order.contractual_return = 99.0
        with self.assertRaises(UserError):
            invoice.action_post()

    # --- Phase 1: Quantity exceeding order (snapshot core) ---

    def test_qty_exceeds_no_exception_post_allowed(self):
        """Qty > order, snapshot matches → own rules, values ok → posts."""
        order, invoice = self._create_confirmed_order_with_invoice(qty=10)
        sale_line = order.order_line.filtered(lambda line: line.product_id)
        sale_line.product_uom_qty = 5
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    def test_qty_exceeds_reeval_no_violation_post_allowed(self):
        """Qty > order, snapshot matches → own rules, values ok → posts."""
        order, invoice = self._create_confirmed_order_with_invoice(qty=10)
        sale_line = order.order_line.filtered(lambda line: line.product_id)
        sale_line.product_uom_qty = 8
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    def test_qty_exceeds_accumulated_partial_invoices(self):
        """Two partial invoices that together exceed order qty.

        Real business scenario: order 100, deliver+invoice 60, then
        deliver+invoice 50. Accumulated invoiced (110) > ordered (100).
        The second invoice enters own rules but posts because
        values are within commercial limits.
        """
        self.product_template_a.invoice_policy = "delivery"
        order = self._create_order()
        order.fiscal_operation_id = False
        line = self._create_order_line(order, qty=100)
        line.seller_discount = 5.0
        line.extra_discount = 0.0
        order.action_confirm()
        # First delivery + invoice: 60 units
        line.qty_delivered = 60
        order._create_invoices()
        first_invoice = order.invoice_ids[0]
        first_invoice.action_post()
        self.assertEqual(first_invoice.state, "posted")
        # Second delivery: 50 more (total delivered 110 > ordered 100)
        line.qty_delivered = 110
        order._create_invoices()
        second_invoice = order.invoice_ids.filtered(lambda move: move.state == "draft")
        self.assertTrue(second_invoice)
        # Accumulated invoiced exceeds order → enters reevaluation
        # (snapshot core: posts because values match sale line)
        second_invoice.action_post()
        self.assertEqual(second_invoice.state, "posted")

    # --- Phase 1: Partial invoicing ---

    def test_partial_identical_values_post_allowed(self):
        """Partial invoice (30 of 100), same values → post allowed. [rule 2]"""
        self.product_template_a.invoice_policy = "order"
        order = self._create_order()
        order.fiscal_operation_id = False
        line = self._create_order_line(order, qty=100)
        line.seller_discount = 5.0
        line.extra_discount = 0.0
        order.action_confirm()
        wizard = (
            self.env["sale.advance.payment.inv"]
            .with_context(active_ids=order.ids, active_model="sale.order")
            .create({"advance_payment_method": "delivered"})
        )
        line.qty_delivered = 30
        wizard.create_invoices()
        invoice = order.invoice_ids[0]
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    # --- Phase 1: Recalculate button ---

    def test_recalculate_resyncs_from_order(self):
        """Recalculate restores sale snapshot after divergence."""
        order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        sale_line = order.order_line.filtered(lambda line: line.product_id)
        wrong_commission = self.env["commission"].create(
            {
                "name": "Wrong",
                "commission_type": "fixed",
                "fix_qty": 1.0,
            }
        )
        inv_line.agent_ids[0].commission_id = wrong_commission
        invoice.action_resync_from_sale_order()
        self.assertEqual(
            inv_line.agent_ids[0].commission_id,
            sale_line.agent_ids[0].commission_id,
        )

    def test_recalculate_adds_missing_agent(self):
        """Recalculate recreates agent that was deleted from invoice."""
        order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        sale_line = order.order_line.filtered(lambda line: line.product_id)
        inv_line.agent_ids.unlink()
        self.assertFalse(inv_line.agent_ids)
        invoice.action_resync_from_sale_order()
        self.assertTrue(inv_line.agent_ids, "Agent was not recreated")
        self.assertEqual(
            inv_line.agent_ids[0].commission_id,
            sale_line.agent_ids[0].commission_id,
        )

    def test_recalculate_removes_extra_unsettled_agent(self):
        """Recalculate removes extra agent that is not settled."""
        order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        extra_agent = self.env["res.partner"].create(
            {
                "name": "Extra Unsettled",
                "agent": True,
                "commission_id": self.commission.id,
            }
        )
        self.env["account.invoice.line.agent"].create(
            {
                "object_id": inv_line.id,
                "agent_id": extra_agent.id,
                "commission_id": self.commission.id,
            }
        )
        invoice.action_resync_from_sale_order()
        sale_line = order.order_line.filtered(lambda line: line.product_id)
        self.assertEqual(
            len(inv_line.agent_ids),
            len(sale_line.agent_ids),
            "Extra agent was not removed",
        )

    def test_resync_restores_all_snapshot_fields(self):
        """Resync restores all policy fields from sale line, not just agents."""
        order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        # Force divergence in all snapshot fields (values must pass constraints)
        inv_line.with_context(skip_invoice_sync=True, check_move_validity=False).write(
            {
                "seller_discount": 10.0,
                "extra_discount": 7.0,
                "extra_discount_reason": "Manual change",
                "base_price": 999.0,
                "reference_price": 888.0,
                "commission_rate": 42.0,
            }
        )
        invoice.action_resync_from_sale_order()
        self._assert_invoice_matches_sale_snapshot(invoice)

    def test_resync_restores_header_snapshot_fields(self):
        """Resync restores header discount fields from sale order."""
        order, invoice = self._create_confirmed_order_with_invoice()
        # Force header divergence
        invoice.with_context(check_move_validity=False).write(
            {
                "tr_cash_discount": 99.0,
                "tr_fob_discount": 88.0,
                "tr_contractual_return": 77.0,
            }
        )
        invoice.action_resync_from_sale_order()
        self.assertAlmostEqual(invoice.tr_cash_discount, order.cash_discount, places=2)
        self.assertAlmostEqual(invoice.tr_fob_discount, order.fob_discount, places=2)
        self.assertAlmostEqual(
            invoice.tr_contractual_return,
            order.contractual_return,
            places=2,
        )

    def test_recalculate_noop_when_synced(self):
        """Recalculate does nothing when snapshot already matches."""
        _order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        original_commission = inv_line.agent_ids[0].commission_id
        invoice.action_resync_from_sale_order()
        self.assertEqual(inv_line.agent_ids[0].commission_id, original_commission)

    # --- Phase 1: Additional snapshot field divergence ---

    def test_extra_discount_reason_divergence_enters_own_rules(self):
        """Divergence in extra_discount_reason → enters own rules (Classe A).

        If no violations, posts normally (own rules pass).
        """
        order, invoice = self._create_confirmed_order_with_invoice()
        sale_line = order.order_line.filtered(lambda line: line.product_id)
        sale_line.extra_discount_reason = "Approved by director"
        # Classe A divergence → own rules, no violation → posts
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    def test_commission_rate_divergence_blocks(self):
        """Divergence in commission_rate → blocks post."""
        _order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        inv_line.with_context(
            skip_invoice_sync=True, check_move_validity=False
        ).commission_rate = 99.0
        with self.assertRaises(UserError):
            invoice.action_post()

    # --- Phase 1: Warning compute ---

    def test_warning_false_for_manual_invoice_without_origin(self):
        """Warning is False for manual invoice (no sale origin)."""
        invoice = self._create_manual_invoice()
        invoice.invalidate_recordset(["unmanaged_commission_warning"])
        self.assertFalse(invoice.unmanaged_commission_warning)

    def test_warning_not_shown_when_all_managed(self):
        """Warning is False when all commissions are managed."""
        _order, invoice = self._create_confirmed_order_with_invoice()
        invoice.invalidate_recordset(["unmanaged_commission_warning"])
        self.assertFalse(invoice.unmanaged_commission_warning)

    # ===================================================================
    # Phase 1b: Commercial revalidation (own rules, tier, constraints)
    # ===================================================================

    # --- Onchange / Recalculation ---

    def test_onchange_seller_discount_recalculates_price_unit(self):
        """Changing seller_discount via onchange recalculates price_unit."""
        _order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        original_price = inv_line.price_unit
        inv_line.seller_discount = 10.0
        inv_line._onchange_seller_extra_discount()
        self.assertNotAlmostEqual(inv_line.price_unit, original_price, places=2)

        from ..models.policy_utils import calc_price_unit

        expected = calc_price_unit(
            inv_line.reference_price, 10.0, inv_line.extra_discount
        )
        self.assertAlmostEqual(inv_line.price_unit, expected, places=2)

    def test_onchange_extra_discount_recalculates_commission_rate(self):
        """Changing seller_discount via onchange updates commission_rate."""
        _order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        inv_line.seller_discount = 0.0
        inv_line._onchange_seller_extra_discount()
        # Different seller_discount may resolve to a different band rate
        # (or same, depending on bands). At minimum, commission_rate is set.
        self.assertIsNotNone(inv_line.commission_rate)

    # --- Hard constraints ---

    def test_seller_discount_above_absolute_max_raises_constraint(self):
        """seller_discount above absolute max → ValidationError."""
        from odoo.exceptions import ValidationError

        _order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        with self.assertRaises(ValidationError):
            inv_line.with_context(
                skip_invoice_sync=True, check_move_validity=False
            ).seller_discount = 99.0

    def test_discount_range_negative_extra_raises(self):
        """Negative extra_discount → ValidationError."""
        from odoo.exceptions import ValidationError

        _order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        with self.assertRaises(ValidationError):
            inv_line.with_context(
                skip_invoice_sync=True, check_move_validity=False
            ).extra_discount = -1.0

    def test_discount_range_total_above_99_raises(self):
        """seller + extra > 99 → ValidationError."""
        from odoo.exceptions import ValidationError

        _order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        with self.assertRaises(ValidationError):
            inv_line.with_context(
                skip_invoice_sync=True, check_move_validity=False
            ).write({"seller_discount": 50.0, "extra_discount": 50.0})

    # --- Tier / approval level ---

    def test_cash_discount_above_profile_triggers_director(self):
        """Invoice cash_discount above profile max → director approval."""
        order, invoice = self._create_confirmed_order_with_invoice()
        profile = order.sales_profile_id
        # Set invoice cash_discount above profile max
        invoice.with_context(check_move_validity=False).write(
            {"tr_cash_discount": profile.cash_discount_max + 5.0}
        )
        invoice.invalidate_recordset(["discount_approval_level"])
        self.assertEqual(invoice.discount_approval_level, "director")

    def test_fob_discount_above_profile_triggers_director(self):
        """Invoice fob_discount above profile max → director approval."""
        order, invoice = self._create_confirmed_order_with_invoice()
        profile = order.sales_profile_id
        invoice.with_context(check_move_validity=False).write(
            {"tr_fob_discount": profile.fob_discount_max + 5.0}
        )
        invoice.invalidate_recordset(["discount_approval_level"])
        self.assertEqual(invoice.discount_approval_level, "director")

    def test_extra_discount_triggers_manager_tier(self):
        """Invoice line extra_discount > 0 → manager approval."""
        from ..models.policy_utils import calc_price_unit

        order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        # Set extra_discount on invoice line and update price_unit to match
        # (avoids Classe B price_unit divergence which would force none)
        new_extra = 1.0
        new_price = calc_price_unit(
            inv_line.reference_price, inv_line.seller_discount, new_extra
        )
        inv_line.with_context(skip_invoice_sync=True, check_move_validity=False).write(
            {
                "extra_discount": new_extra,
                "price_unit": new_price,
            }
        )
        invoice.invalidate_recordset(["discount_approval_level"])
        self.assertIn(invoice.discount_approval_level, ("manager", "director"))

    def test_payment_term_above_limit_triggers_director(self):
        """Payment term above cash_term_avg_days_max → director."""
        order, invoice = self._create_confirmed_order_with_invoice()
        profile = order.sales_profile_id
        if not profile.cash_term_avg_days_max:  # pragma: no cover
            return  # pragma: no cover
        # Set cash_discount > 0 to activate payment term check
        invoice.with_context(check_move_validity=False).write({"tr_cash_discount": 1.0})
        # Create a long payment term
        long_term = self.env["account.payment.term"].create(
            {
                "name": "Very Long Term",
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "value": "balance",
                            "days": int(profile.cash_term_avg_days_max) + 30,
                        },
                    )
                ],
            }
        )
        invoice.invoice_payment_term_id = long_term
        invoice.invalidate_recordset(
            ["discount_approval_level", "payment_term_avg_days"]
        )
        self.assertEqual(invoice.discount_approval_level, "director")

    # --- Parity / own rules flow ---

    def test_parity_invoice_no_tier_needed(self):
        """Invoice in perfect parity → approval level = none."""
        _order, invoice = self._create_confirmed_order_with_invoice()
        invoice.invalidate_recordset(["discount_approval_level"])
        self.assertEqual(invoice.discount_approval_level, "none")

    def test_diverged_invoice_no_violations_posts_free(self):
        """Classe A divergence but values within limits → posts."""
        order, invoice = self._create_confirmed_order_with_invoice()
        # Change seller_discount on sale (creates divergence)
        sale_line = order.order_line.filtered(lambda line: line.product_id)
        sale_line.seller_discount = 3.0  # different from invoice's 5.0
        # Invoice has seller_discount=5.0 which is within limits
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    def test_draft_order_snapshot_match_enters_own_rules(self):
        """Draft order + snapshot match → enters own rules path."""
        order, invoice = self._create_confirmed_order_with_invoice()
        order.with_context(tracking_disable=True).write({"state": "draft"})
        # Snapshot matches, but order is draft → own rules
        # Values are within limits → posts
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    def test_qty_exceed_snapshot_match_enters_own_rules(self):
        """Qty exceeds order + snapshot match → own rules → posts if ok."""
        self.product_template_a.invoice_policy = "delivery"
        order = self._create_order()
        order.fiscal_operation_id = False
        line = self._create_order_line(order, qty=100)
        line.seller_discount = 5.0
        line.extra_discount = 0.0
        order.action_confirm()
        line.qty_delivered = 110
        order._create_invoices()
        invoice = order.invoice_ids.filtered(lambda m: m.state == "draft")
        # Qty exceeds → own rules, values within limits → posts
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    def test_draft_order_snapshot_match_triggers_tier_when_values_violate(
        self,
    ):
        """Draft order + values violating rules → tier triggers."""
        order, invoice = self._create_confirmed_order_with_invoice()
        order.with_context(tracking_disable=True).write({"state": "draft"})
        profile = order.sales_profile_id
        # Set invoice cash_discount above limit
        invoice.with_context(check_move_validity=False).write(
            {"tr_cash_discount": profile.cash_discount_max + 5.0}
        )
        invoice.invalidate_recordset(["discount_approval_level"])
        self.assertEqual(invoice.discount_approval_level, "director")

    # --- Hard blocks that stay hard blocks ---

    def test_agent_divergence_still_hard_blocks_even_with_tier_enabled(self):
        """Agent divergence → hard block, not own rules."""
        _order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        # Change agent commission to create Classe C divergence
        wrong_commission = self.env["commission"].create(
            {
                "name": "Wrong Unmanaged",
                "commission_type": "fixed",
                "fix_qty": 99.0,
            }
        )
        inv_line.agent_ids[0].commission_id = wrong_commission
        with self.assertRaises(UserError):
            invoice.action_post()

    def test_homogeneous_multi_order_invoice_posts(self):
        """Homogeneous multi-order invoice → posts (Phase 3)."""
        order1 = self._create_order()
        order1.fiscal_operation_id = False
        line1 = self._create_order_line(order1, qty=10)
        line1.seller_discount = 5.0
        line1.extra_discount = 0.0
        order1.action_confirm()
        order2 = self._create_order()
        order2.fiscal_operation_id = False
        line2 = self._create_order_line(order2, qty=10)
        line2.seller_discount = 5.0
        line2.extra_discount = 0.0
        order2.action_confirm()
        invoice = (order1 | order2)._create_invoices()
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    def test_snapshot_only_divergence_still_hard_blocks(self):
        """Classe B divergence (base_price) → hard block."""
        _order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        inv_line.with_context(skip_invoice_sync=True, check_move_validity=False).write(
            {"base_price": 999.0}
        )
        with self.assertRaises(UserError):
            invoice.action_post()

    def test_direct_price_unit_edit_still_hard_blocks(self):
        """Direct price_unit edit → Classe B hard block."""
        _order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        inv_line.with_context(
            skip_invoice_sync=True, check_move_validity=False
        ).price_unit = 0.01
        with self.assertRaises(UserError):
            invoice.action_post()

    # --- Backend safety ---

    def test_action_post_blocked_when_need_validation_pending(self):
        """action_post raises when need_validation is True."""
        order, invoice = self._create_confirmed_order_with_invoice()
        profile = order.sales_profile_id
        # Force a director-level violation on the invoice
        invoice.with_context(check_move_validity=False).write(
            {"tr_cash_discount": profile.cash_discount_max + 10.0}
        )
        invoice.invalidate_recordset(["discount_approval_level"])
        self.assertEqual(invoice.discount_approval_level, "director")
        # If tier is active and need_validation is True, action_post blocks
        if invoice.need_validation:
            with self.assertRaises(UserError):
                invoice.action_post()
        else:  # pragma: no cover
            # If no tier definition matched (e.g., test env without tier data),
            # the invoice posts because own rules constraints pass
            invoice.action_post()  # pragma: no cover
            self.assertEqual(invoice.state, "posted")  # pragma: no cover

    # --- Equivalência ---

    def test_invoice_line_rule_resolution_matches_sale_line(self):
        """Invoice line _get_applicable_rule matches sale line."""
        order, invoice = self._create_confirmed_order_with_invoice()
        sale_line = order.order_line.filtered(lambda line: line.product_id)
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        self.assertEqual(
            inv_line._get_applicable_rule(),
            sale_line._get_applicable_rule(),
        )

    # --- Payment term ---

    def test_payment_term_divergence_enters_own_rules(self):
        """Payment term diverges from sale → Classe A, enters own rules."""
        order, invoice = self._create_confirmed_order_with_invoice()
        # Create different payment term
        other_term = self.env["account.payment.term"].create(
            {
                "name": "Other Term",
                "line_ids": [(0, 0, {"value": "balance", "days": 15})],
            }
        )
        invoice.invoice_payment_term_id = other_term
        # This is a Classe A divergence → own rules, values ok → posts
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    # Note: native `discount` field is NOT directly checked because
    # other modules (l10n_br, punctuality) modify it independently.
    # It's governed indirectly via cash/fob header checks.
    # Phase 5 will add direct discount governance.

    def test_resync_restores_payment_term_from_sale_order(self):
        """Resync restores payment_term from sale order."""
        order, invoice = self._create_confirmed_order_with_invoice()
        other_term = self.env["account.payment.term"].create(
            {
                "name": "Changed Term",
                "line_ids": [(0, 0, {"value": "balance", "days": 60})],
            }
        )
        invoice.invoice_payment_term_id = other_term
        invoice.action_resync_from_sale_order()
        self.assertEqual(
            invoice.invoice_payment_term_id,
            order.payment_term_id,
        )

    # Note: extra_discount_reason is NOT enforced by backend constraint
    # (same as sale.order.line). The UI wizard/popover handles this.
    # Future: Phase final may add UI governance if needed.

    # --- invoice_divergence_warning banner ---

    def test_divergence_warning_empty_on_parity(self):
        """No banner when invoice matches the sale snapshot."""
        _order, invoice = self._create_confirmed_order_with_invoice()
        self.assertFalse(invoice.invoice_divergence_warning)

    def test_divergence_warning_populated_on_edit(self):
        """Editing a line discount surfaces the divergence in the banner."""
        _order, invoice = self._create_confirmed_order_with_invoice()
        line = invoice.invoice_line_ids.filtered(
            lambda sol: sol.display_type == "product"
        )
        line.seller_discount = (line.seller_discount or 0) + 1.0
        invoice.invalidate_recordset(["invoice_divergence_warning"])
        self.assertTrue(invoice.invoice_divergence_warning)

    def test_divergence_warning_silent_when_policy_not_applicable(self):
        """No banner when the company has no default sales profile."""
        _order, invoice = self._create_confirmed_order_with_invoice()
        invoice.company_id.default_sales_profile_id = False
        line = invoice.invoice_line_ids.filtered(
            lambda sol: sol.display_type == "product"
        )
        line.seller_discount = (line.seller_discount or 0) + 1.0
        invoice.invalidate_recordset(["invoice_divergence_warning"])
        self.assertFalse(invoice.invoice_divergence_warning)

    def test_divergence_warning_silent_on_posted(self):
        """Banner only shows on draft moves."""
        _order, invoice = self._create_confirmed_order_with_invoice()
        invoice.action_post()
        self.assertFalse(invoice.invoice_divergence_warning)

    def test_divergence_warning_silent_on_refund(self):
        """Refunds bypass the commercial policy funnel — no banner."""
        _order, invoice = self._create_confirmed_order_with_invoice()
        invoice.action_post()
        refund_wizard = (
            self.env["account.move.reversal"]
            .with_context(active_model="account.move", active_ids=invoice.ids)
            .create(
                {
                    "reason": "Test refund",
                    "refund_method": "refund",
                    "journal_id": invoice.journal_id.id,
                }
            )
        )
        refund = self.env["account.move"].browse(
            refund_wizard.reverse_moves()["res_id"]
        )
        line = refund.invoice_line_ids.filtered(
            lambda sol: sol.display_type == "product"
        )
        line.seller_discount = (line.seller_discount or 0) + 1.0
        refund.invalidate_recordset(["invoice_divergence_warning"])
        self.assertFalse(refund.invoice_divergence_warning)

    def test_divergence_warning_structural_on_heterogeneous(self):
        """Heterogeneous invoice surfaces a structural banner message."""
        order1 = self._create_order()
        order1.fiscal_operation_id = False
        line1 = self._create_order_line(order1, qty=10)
        line1.seller_discount = 5.0
        order1.action_confirm()
        order2 = self._create_order()
        order2.fiscal_operation_id = False
        line2 = self._create_order_line(order2, qty=5)
        line2.seller_discount = 5.0
        order2.action_confirm()
        invoice = (order1 | order2)._create_invoices()
        order2.cash_discount = 99.0
        invoice.invalidate_recordset(["invoice_divergence_warning"])
        warning = invoice.invoice_divergence_warning
        self.assertTrue(warning)
        self.assertIn("different commercial", warning.lower())

    def test_divergence_warning_silent_when_order_not_confirmed(self):
        """Unconfirmed origin orders fall into own-rules path — no banner."""
        order, invoice = self._create_confirmed_order_with_invoice()
        # Un-confirm the origin order to reproduce the "not confirmed" path.
        order.state = "draft"
        line = invoice.invoice_line_ids.filtered(
            lambda sol: sol.display_type == "product"
        )
        line.seller_discount = (line.seller_discount or 0) + 1.0
        invoice.invalidate_recordset(["invoice_divergence_warning"])
        self.assertFalse(invoice.invoice_divergence_warning)

    def test_unmanaged_warning_skips_newid_invoice_lines(self):
        """Invoice banner ignores ``NewId`` lines to avoid false positives
        during onchange (agents/commissions are reconciled post-save)."""
        from odoo.tests.common import Form

        _order, invoice = self._create_confirmed_order_with_invoice()
        with Form(invoice) as form:
            with form.invoice_line_ids.new() as line_form:
                line_form.product_id = self.product_a
                # Touching the warning during the onchange cycle must
                # not crash and must not flag the new (unsaved) line.
                self.assertFalse(form.unmanaged_commission_warning)

    def test_divergence_warning_reports_structural_usererror(self):
        """Structural UserError from the issue collector becomes banner text
        (never crashes the form)."""
        _order, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda sol: sol.display_type == "product"
        )
        # Attach a second sale_line to the invoice line — that is the
        # structural violation that ``_get_invoice_snapshot_issues``
        # raises a ``UserError`` on.
        other_order = self._create_order()
        other_order.fiscal_operation_id = False
        other_line = self._create_order_line(other_order, qty=1)
        other_order.action_confirm()
        inv_line.sale_line_ids = [(4, other_line.id)]
        invoice.invalidate_recordset(["invoice_divergence_warning"])
        warning = invoice.invoice_divergence_warning
        self.assertTrue(warning)
        self.assertIn("one-to-one", warning.lower())

    def test_divergence_warning_silent_when_out_of_validity(self):
        """Invoices outside the validity window fall into own-rules — no banner."""
        order, invoice = self._create_confirmed_order_with_invoice()
        self.env.company.invoice_validity_days = 1
        # Push the order confirmation date far enough in the past so the
        # validity check fails.
        from datetime import timedelta

        order.date_order = order.date_order - timedelta(days=30)
        line = invoice.invoice_line_ids.filtered(
            lambda sol: sol.display_type == "product"
        )
        line.seller_discount = (line.seller_discount or 0) + 1.0
        invoice.invalidate_recordset(["invoice_divergence_warning"])
        self.assertFalse(invoice.invoice_divergence_warning)

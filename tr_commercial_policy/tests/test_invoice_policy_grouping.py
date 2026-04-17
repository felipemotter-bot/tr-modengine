# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestInvoicePolicyGrouping(CommercialPolicyTestCommon):
    """Tests for multi-order invoice grouping (Phase 3).

    Verifies that orders with different commercial contexts produce
    separate invoices, and that homogeneous orders group correctly.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()
        cls.product_template_a.invoice_policy = "order"
        cls.product_template_b.invoice_policy = "order"

    def _create_two_homogeneous_orders(self):
        """Create 2 orders with identical commercial context."""
        order1 = self._create_order()
        order1.fiscal_operation_id = False
        line1 = self._create_order_line(order1, qty=10)
        line1.seller_discount = 5.0
        line1.extra_discount = 0.0
        order1.action_confirm()

        order2 = self._create_order()
        order2.fiscal_operation_id = False
        line2 = self._create_order_line(order2, qty=5)
        line2.seller_discount = 5.0
        line2.extra_discount = 0.0
        order2.action_confirm()
        return order1, order2

    # --- Grouping / split ---

    def test_same_context_orders_grouped_into_one_invoice(self):
        """Two orders with same commercial context → 1 invoice."""
        order1, order2 = self._create_two_homogeneous_orders()
        invoices = (order1 | order2)._create_invoices()
        self.assertEqual(len(invoices), 1)

    def test_different_cash_discount_splits_invoices(self):
        """Different cash_discount → separate invoices."""
        order1, order2 = self._create_two_homogeneous_orders()
        # Change cash on order2 after confirm
        order2.cash_discount = 99.0
        invoices = (order1 | order2)._create_invoices()
        self.assertGreater(len(invoices), 1)

    def test_different_fob_discount_splits_invoices(self):
        """Different fob_discount → separate invoices."""
        order1, order2 = self._create_two_homogeneous_orders()
        order2.fob_discount = 99.0
        invoices = (order1 | order2)._create_invoices()
        self.assertGreater(len(invoices), 1)

    def test_different_contractual_return_splits_invoices(self):
        """Different contractual_return → separate invoices."""
        order1, order2 = self._create_two_homogeneous_orders()
        order2.contractual_return = 99.0
        invoices = (order1 | order2)._create_invoices()
        self.assertGreater(len(invoices), 1)

    def test_different_profile_splits_invoices(self):
        """Different sales_profile_id → separate invoices."""
        order1, order2 = self._create_two_homogeneous_orders()
        other_profile = self.env["tr.sales.profile"].create(
            {
                "name": "Other Profile",
                "profile_type": "agent",
                "rule_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "general",
                            "commission_band_ids": [
                                (0, 0, {"discount_up_to": 0.0, "commission_rate": 0.0}),
                            ],
                        },
                    ),
                ],
            }
        )
        order2.sales_profile_id = other_profile
        invoices = (order1 | order2)._create_invoices()
        self.assertGreater(len(invoices), 1)

    # --- Post / flow ---

    def test_homogeneous_multi_order_posts_free(self):
        """Homogeneous multi-order invoice posts without block."""
        order1, order2 = self._create_two_homogeneous_orders()
        invoices = (order1 | order2)._create_invoices()
        self.assertEqual(len(invoices), 1)
        invoices.action_post()
        self.assertEqual(invoices.state, "posted")

    def test_heterogeneous_forced_still_blocks_post(self):
        """Heterogeneous invoice (forced after grouping) blocks post."""
        order1, order2 = self._create_two_homogeneous_orders()
        invoice = (order1 | order2)._create_invoices()
        # Tamper with origin order to create heterogeneity
        order2.cash_discount = 99.0
        self.assertTrue(invoice._has_heterogeneous_sale_origins())
        with self.assertRaises(UserError):
            invoice.action_post()

    def test_heterogeneous_forced_still_blocks_resync(self):
        """Heterogeneous invoice (forced after grouping) blocks resync."""
        order1, order2 = self._create_two_homogeneous_orders()
        invoice = (order1 | order2)._create_invoices()
        order2.cash_discount = 99.0
        self.assertTrue(invoice._has_heterogeneous_sale_origins())
        with self.assertRaises(UserError):
            invoice.action_resync_from_sale_order()

    def test_different_payment_term_splits_invoices(self):
        """Different payment_term_id → separate invoices."""
        order1, order2 = self._create_two_homogeneous_orders()
        other_term = self.env["account.payment.term"].create(
            {
                "name": "Other Term",
                "line_ids": [(0, 0, {"value": "balance", "days": 60})],
            }
        )
        order2.payment_term_id = other_term
        invoices = (order1 | order2)._create_invoices()
        self.assertGreater(len(invoices), 1)

    def test_different_condition_same_values_splits_invoices(self):
        """Different condition_id but same values → still splits.

        Isolates the commercial_condition_id grouping key: two
        conditions with identical cash/fob/contractual/profile/term
        still produce separate invoices because condition IDs differ.
        """
        order1, order2 = self._create_two_homogeneous_orders()
        condition1 = order1.commercial_condition_id
        # Create a different condition with identical values
        other_partner = self.env["res.partner"].create(
            {"name": "Other Customer for condition key test"}
        )
        other_condition = self.env["partner.commercial.condition"].create(
            {
                "partner_id": other_partner.id,
                "pricelist_id": condition1.pricelist_id.id,
                "cash_discount": condition1.cash_discount,
                "fob_discount": condition1.fob_discount,
                "seller_discount": condition1.seller_discount,
                "contractual_return": condition1.contractual_return,
            }
        )
        # Same values, different condition ID
        order2.commercial_condition_id = other_condition
        # Ensure same profile too
        order2.sales_profile_id = order1.sales_profile_id
        invoices = (order1 | order2)._create_invoices()
        self.assertGreater(len(invoices), 1)

    # --- Snapshot / tier ---

    def test_multi_order_snapshot_matches(self):
        """Homogeneous multi-order invoice snapshot matches orders."""
        order1, order2 = self._create_two_homogeneous_orders()
        invoice = (order1 | order2)._create_invoices()
        self._assert_invoice_matches_sale_snapshot(invoice)

    def test_multi_order_homogeneous_tier_triggers(self):
        """cash_discount above limit on homogeneous multi-order → tier."""
        order1, order2 = self._create_two_homogeneous_orders()
        invoice = (order1 | order2)._create_invoices()
        profile = order1.sales_profile_id
        invoice.with_context(check_move_validity=False).write(
            {"tr_cash_discount": profile.cash_discount_max + 5.0}
        )
        invoice.invalidate_recordset(["discount_approval_level"])
        self.assertEqual(invoice.discount_approval_level, "director")

    # --- Onchange / method coverage ---

    def test_onchange_cash_fob_recalculates_line_discount(self):
        """Calling _onchange_cash_fob_discount updates line.discount."""
        order1, order2 = self._create_two_homogeneous_orders()
        invoice = (order1 | order2)._create_invoices()
        lines = invoice._get_policy_invoice_lines()
        self.assertTrue(lines)
        invoice.tr_cash_discount = 7.0
        invoice.tr_fob_discount = 3.0
        invoice._onchange_cash_fob_discount()
        for line in lines:
            self.assertAlmostEqual(line.discount, 10.0, places=2)

    def test_get_origin_sales_profile_no_orders_raises(self):
        """_get_origin_sales_profile raises when no sale orders."""
        invoice = self._create_manual_invoice()
        with self.assertRaises(UserError):
            invoice._get_origin_sales_profile()

    def test_get_origin_sales_profile_heterogeneous_raises(self):
        """_get_origin_sales_profile raises for heterogeneous origins."""
        order1, order2 = self._create_two_homogeneous_orders()
        invoice = (order1 | order2)._create_invoices()
        # Force heterogeneity
        order2.cash_discount = 99.0
        self.assertTrue(invoice._has_heterogeneous_sale_origins())
        with self.assertRaises(UserError):
            invoice._get_origin_sales_profile()

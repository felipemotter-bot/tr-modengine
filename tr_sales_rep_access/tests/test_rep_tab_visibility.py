# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from .common import SalesRepAccessTestCommon


@tagged("post_install", "-at_install")
class TestRepTabVisibility(SalesRepAccessTestCommon):
    """Hide Other Info tab for reps: is_sales_rep_external flag and review_user_count guard."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.order = cls.env["sale.order"].create(
            {
                "partner_id": cls.customer_c1.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": cls.product.id,
                            "product_uom_qty": 1.0,
                            "price_unit": 100.0,
                        },
                    ),
                ],
            }
        )

    # ------------------------------------------------------------------
    # is_sales_rep_external
    # ------------------------------------------------------------------

    def test_is_sales_rep_external_true_for_rep(self):
        order_as_rep = self.order.with_user(self.user_u1)
        self.assertTrue(order_as_rep.is_sales_rep_external)

    def test_is_sales_rep_external_false_for_non_rep(self):
        self.assertFalse(self.order.sudo().is_sales_rep_external)

    # ------------------------------------------------------------------
    # review_user_count short-circuit (res.users override)
    # ------------------------------------------------------------------

    def test_review_user_count_returns_empty_list_for_rep(self):
        result = self.env["res.users"].with_user(self.user_u1).review_user_count()
        self.assertEqual(result, [])

    def test_review_user_count_calls_super_for_non_rep(self):
        result = self.env["res.users"].sudo().review_user_count()
        self.assertIsInstance(result, list)

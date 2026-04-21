# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import SalesRepAccessTestCommon

_STOCK_QTY_FIELDS = (
    "qty_available",
    "virtual_available",
    "incoming_qty",
    "outgoing_qty",
)
_SALE_LINE_STOCK_FIELDS = (
    "display_qty_widget",
    "virtual_available_at_date",
    "qty_available_today",
    "free_qty_today",
    "forecast_expected_date",
    "scheduled_date",
)


@tagged("post_install", "-at_install")
class TestRepStockHide(SalesRepAccessTestCommon):
    """PR 9 — stock quantities and widgets invisible for external reps.

    Strategy: field-level groups= on qty_available/virtual_available/
    incoming_qty/outgoing_qty (product.template + product.product) and
    on the sale_stock computed fields (sale.order.line) blocks RPC
    read/fields_get.  View XML hides the qty_at_date_widget in the
    order form.  The Stock root menu is hidden via ir.ui.menu override.
    """

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
    # product.template — field-level block
    # ------------------------------------------------------------------

    def test_rep_stock_qty_fields_not_in_fields_get_template(self):
        info = self.env["product.template"].with_user(self.user_u1).fields_get()
        leaked = [f for f in _STOCK_QTY_FIELDS if f in info]
        self.assertFalse(
            leaked,
            f"fields_get must not expose stock qty fields for rep on product.template, leaked: {leaked}",
        )

    def test_rep_cannot_read_qty_available_on_template(self):
        with self.assertRaises(
            AccessError,
            msg="Rep must not be able to read qty_available on product.template",
        ):
            self.product.with_user(self.user_u1).read(["qty_available"])

    # ------------------------------------------------------------------
    # product.product — field-level block
    # ------------------------------------------------------------------

    def test_rep_stock_qty_fields_not_in_fields_get_product(self):
        info = self.env["product.product"].with_user(self.user_u1).fields_get()
        leaked = [f for f in _STOCK_QTY_FIELDS if f in info]
        self.assertFalse(
            leaked,
            f"fields_get must not expose stock qty fields for rep on product.product, leaked: {leaked}",
        )

    def test_rep_cannot_read_qty_available_on_product(self):
        with self.assertRaises(
            AccessError,
            msg="Rep must not be able to read qty_available on product.product",
        ):
            self.product.product_variant_ids[:1].with_user(self.user_u1).read(
                ["qty_available"]
            )

    # ------------------------------------------------------------------
    # Regression: admin is unaffected
    # ------------------------------------------------------------------

    def test_admin_can_read_qty_available_on_template(self):
        values = self.product.sudo().read(["qty_available"])
        self.assertEqual(len(values), 1)
        self.assertIn("qty_available", values[0])

    # ------------------------------------------------------------------
    # sale.order.line — sale_stock computed fields
    # ------------------------------------------------------------------

    def test_rep_stock_fields_not_in_fields_get_sale_order_line(self):
        info = self.env["sale.order.line"].with_user(self.user_u1).fields_get()
        leaked = [f for f in _SALE_LINE_STOCK_FIELDS if f in info]
        self.assertFalse(
            leaked,
            f"fields_get must not expose sale_stock fields for rep on sale.order.line, leaked: {leaked}",
        )

    def test_rep_cannot_read_stock_field_on_order_line(self):
        line = self.order.order_line[:1]
        with self.assertRaises(
            AccessError,
            msg="Rep must not be able to read virtual_available_at_date on sale.order.line",
        ):
            line.with_user(self.user_u1).read(["virtual_available_at_date"])

    # Note: the qty_at_date_widget lives inside the order_line inline
    # sub-view which is processed independently; groups= on <widget>
    # nodes in that context is not handled by the parent form's
    # _postprocess_access_rights pass.  The real security barrier is
    # field-level groups= (tests above): the widget renders empty
    # because it cannot read virtual_available_at_date et al.

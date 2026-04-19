# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import ValidationError

from .common import SalesRepAccessTestCommon


class TestCatalogConstraint(SalesRepAccessTestCommon):
    def _order_for_rep(self, customer):
        """Create an empty draft order as admin for the given customer."""
        return self.env["sale.order"].create({"partner_id": customer.id})

    def test_rep_can_add_line_with_allowed_product(self):
        """Rep adding a line with a product in catalog passes."""
        order = self._order_for_rep(self.customer_c1)
        line = (
            self.env["sale.order.line"]
            .with_user(self.user_u1)
            .create(
                {
                    "order_id": order.id,
                    "product_id": self.product.id,
                    "product_uom_qty": 1.0,
                }
            )
        )
        self.assertTrue(line.exists())

    def test_rep_cannot_add_line_with_excluded_product(self):
        """Rep adding a line with a product in excluded subtree fails."""
        order = self._order_for_rep(self.customer_c1)
        with self.assertRaises(ValidationError):
            self.env["sale.order.line"].with_user(self.user_u1).create(
                {
                    "order_id": order.id,
                    "product_id": self.product_excluded.id,
                    "product_uom_qty": 1.0,
                }
            )

    def test_rep_cannot_add_line_with_out_of_scope_product(self):
        """Rep adding a line with a product outside allowed fails."""
        order = self._order_for_rep(self.customer_c1)
        with self.assertRaises(ValidationError):
            self.env["sale.order.line"].with_user(self.user_u1).create(
                {
                    "order_id": order.id,
                    "product_id": self.product_other.id,
                    "product_uom_qty": 1.0,
                }
            )

    def test_rep_cannot_switch_existing_line_to_excluded_product(self):
        """Changing product_id of an existing line to excluded fails."""
        order = self._make_order(self.customer_c1)
        line = order.order_line[0]
        with self.assertRaises(ValidationError):
            line.with_user(self.user_u1).write({"product_id": self.product_excluded.id})

    def test_non_rep_user_can_add_any_product(self):
        """Admin (non-rep) is not affected by the catalog constraint."""
        order = self._order_for_rep(self.customer_c1)
        line = self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": self.product_other.id,
                "product_uom_qty": 1.0,
            }
        )
        self.assertTrue(line.exists())

    def test_rep_with_empty_catalog_blocks_any_product(self):
        """Rep whose catalog is empty cannot add any product."""
        order = self.env["sale.order"].create({"partner_id": self.customer_c2.id})
        with self.assertRaises(ValidationError):
            self.env["sale.order.line"].with_user(self.user_u2).create(
                {
                    "order_id": order.id,
                    "product_id": self.product.id,
                    "product_uom_qty": 1.0,
                }
            )

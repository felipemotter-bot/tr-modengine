# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError

from .common import SalesRepAccessTestCommon


class TestRpcBlocks(SalesRepAccessTestCommon):
    def test_export_data_raises_for_rep(self):
        """Rep cannot call export_data on any model."""
        Partner = self.env["res.partner"].with_user(self.user_u1)
        with self.assertRaises(AccessError):
            Partner.search([], limit=1).export_data(["name"])

        SaleOrder = self.env["sale.order"].with_user(self.user_u1)
        with self.assertRaises(AccessError):
            SaleOrder.search([], limit=1).export_data(["name"])

    def test_export_data_allowed_for_admin(self):
        """Admin (not in rep group) still can export."""
        admin = self.env.ref("base.user_admin")
        Partner = self.env["res.partner"].with_user(admin)
        # Should not raise — ensure our override does not regress for
        # other users.
        Partner.search([], limit=1).export_data(["name"])

    def test_rep_cannot_write_to_confirmed_order_via_rpc(self):
        """RPC write on a confirmed order in scope raises AccessError."""
        order = self._make_order(self.customer_c1)
        order.action_confirm()
        with self.assertRaises(AccessError):
            order.with_user(self.user_u1).write({"client_order_ref": "SHOULD-FAIL"})

    def test_rep_cannot_unlink_order_via_rpc(self):
        """RPC unlink on any order in scope raises AccessError."""
        order = self._make_order(self.customer_c1)
        with self.assertRaises(AccessError):
            order.with_user(self.user_u1).unlink()

    def test_rep_cannot_write_to_confirmed_line(self):
        """sale.order.line write blocked once order is confirmed."""
        order = self._make_order(self.customer_c1)
        line = order.order_line[0]
        order.action_confirm()
        with self.assertRaises(AccessError):
            line.with_user(self.user_u1).write({"product_uom_qty": 5.0})

    def test_rep_can_unlink_line_in_draft(self):
        """Rep can remove a line while order is still in draft."""
        order = self._make_order(self.customer_c1)
        line = order.order_line[0]
        line.with_user(self.user_u1).unlink()
        self.assertFalse(order.order_line)

    def test_rep_cannot_unlink_confirmed_line(self):
        """Rep cannot remove a line once the order is confirmed."""
        order = self._make_order(self.customer_c1)
        line = order.order_line[0]
        order.action_confirm()
        with self.assertRaises(AccessError):
            line.with_user(self.user_u1).unlink()

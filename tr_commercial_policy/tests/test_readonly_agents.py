# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestReadonlyAgents(CommercialPolicyTestCommon):
    """Test that non-directors see agent editor in readonly mode."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    def test_sale_line_agents_readonly_for_salesperson(self):
        """Salesperson opens agent editor → readonly."""
        order = self._create_order()
        order.fiscal_operation_id = False
        line = self._create_order_line(order, qty=1)
        action = line.with_user(self.salesperson).button_edit_agents()
        self.assertEqual(action["flags"]["mode"], "readonly")

    def test_sale_line_agents_editable_for_director(self):
        """Director opens agent editor → no readonly flag."""
        order = self._create_order()
        order.fiscal_operation_id = False
        line = self._create_order_line(order, qty=1)
        action = line.with_user(self.director_user).button_edit_agents()
        self.assertNotIn("flags", action)

    def test_invoice_line_agents_readonly_for_salesperson(self):
        """Salesperson opens invoice agent editor → readonly."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        action = inv_line.with_user(self.salesperson).button_edit_agents()
        self.assertEqual(action["flags"]["mode"], "readonly")

    def test_invoice_line_agents_editable_for_director(self):
        """Director opens invoice agent editor → no readonly flag."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        action = inv_line.with_user(self.director_user).button_edit_agents()
        self.assertNotIn("flags", action)

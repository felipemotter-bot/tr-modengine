# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestPropagation(CommercialPolicyTestCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        # Set up contractual return on condition
        cls.condition.contractual_return = 3.0
        # Products must be invoiceable on ordered quantities
        cls.product_template_a.invoice_policy = "order"
        cls.product_template_b.invoice_policy = "order"

    def _create_confirmed_order(self):
        """Create and confirm an order for propagation tests."""
        order = self._create_order()
        # Clear fiscal operation to use standard invoicing path
        order.fiscal_operation_id = False
        line = self._create_order_line(order, base_price=100.0)
        line.seller_discount = 5.0
        line.extra_discount = 0.0
        line.discount_fixed = True
        order.action_confirm()
        return order, line

    def test_prepare_invoice_receives_cash_discount(self):
        """Test that _prepare_invoice includes cash_discount."""
        order, _line = self._create_confirmed_order()
        vals = order._prepare_invoice()
        self.assertAlmostEqual(
            vals.get("tr_cash_discount", 0),
            order.cash_discount,
            places=2,
        )

    def test_prepare_invoice_receives_fob_discount(self):
        """Test that _prepare_invoice includes fob_discount."""
        order, _line = self._create_confirmed_order()
        vals = order._prepare_invoice()
        self.assertAlmostEqual(
            vals.get("tr_fob_discount", 0),
            order.fob_discount,
            places=2,
        )

    def test_prepare_invoice_receives_contractual_return(self):
        """Test that _prepare_invoice includes contractual_return."""
        order, _line = self._create_confirmed_order()
        vals = order._prepare_invoice()
        self.assertAlmostEqual(
            vals.get("tr_contractual_return", 0),
            order.contractual_return,
            places=2,
        )

    def test_invoice_line_price_unit_matches(self):
        """Test that _prepare_invoice_line carries price_unit from SO line."""
        _order, line = self._create_confirmed_order()
        vals = line._prepare_invoice_line()
        self.assertAlmostEqual(
            vals.get("price_unit", 0),
            line.price_unit,
            places=2,
        )

    def test_invoice_line_discount_matches(self):
        """Test that _prepare_invoice_line carries discount from SO line."""
        _order, line = self._create_confirmed_order()
        vals = line._prepare_invoice_line()
        self.assertAlmostEqual(
            vals.get("discount", 0),
            line.discount,
            places=2,
        )

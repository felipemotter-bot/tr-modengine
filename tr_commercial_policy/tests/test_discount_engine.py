# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestDiscountEngine(CommercialPolicyTestCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_base_price_auto_populated(self):
        """Test that base_price is auto-populated from pricelist."""
        order = self._create_order()
        line = self._create_order_line(order)
        # base_price should be set (computed from pricelist/product)
        self.assertTrue(
            line.base_price > 0,
            "base_price should be auto-populated from pricelist",
        )

    def test_price_unit_with_seller_discount(self):
        """Test that price_unit = reference_price * (1 - seller_discount/100)."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        line.seller_discount = 10.0
        line.extra_discount = 0.0
        # reference_price = base_price when no contractual_return
        self.assertAlmostEqual(line.reference_price, 100.0, places=2)
        # price_unit = 100 * (1 - 10/100) = 90
        self.assertAlmostEqual(line.price_unit, 90.0, places=2)

    def test_price_unit_with_seller_and_extra_discount(self):
        """Test that price_unit uses combined seller + extra discount."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        line.seller_discount = 5.0
        line.extra_discount = 3.0
        # price_unit = 100 * (1 - (5+3)/100) = 100 * 0.92 = 92
        self.assertAlmostEqual(line.price_unit, 92.0, places=2)

    def test_discount_field_equals_cash_plus_fob(self):
        """Test that discount = cash_discount + fob_discount."""
        order = self._create_order()
        # condition has cash=2.0, fob=1.0
        line = self._create_order_line(order, base_price=100.0)
        # discount should be 2.0 + 1.0 = 3.0
        self.assertAlmostEqual(line.discount, 3.0, places=2)

    def test_discount_value_computed(self):
        """Test that discount_value is computed from discount %."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        # discount = 3.0%, price_unit ≈ 100 (no seller discount)
        expected_value = line.product_uom_qty * line.price_unit * 3.0 / 100
        self.assertAlmostEqual(line.discount_value, expected_value, places=2)

    def test_condition_resolution_variant_over_template(self):
        """Test that variant-specific condition line overrides template."""
        # Add template line with 3% and variant line with 7%
        self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "product_tmpl_id": self.product_template_a.id,
                "seller_discount": 3.0,
                "extra_discount": 1.0,
            }
        )
        variant_cond_line = self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product",
                "product_id": self.product_a.id,
                "seller_discount": 7.0,
                "extra_discount": 2.0,
            }
        )

        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        order._apply_condition_to_line(line, self.condition)

        self.assertAlmostEqual(
            line.seller_discount,
            variant_cond_line.seller_discount,
            places=2,
        )
        self.assertAlmostEqual(line.extra_discount, 2.0, places=2)

    def test_condition_resolution_template_over_general(self):
        """Test that template-specific line overrides general seller_discount."""
        self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "product_tmpl_id": self.product_template_a.id,
                "seller_discount": 8.0,
            }
        )

        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        order._apply_condition_to_line(line, self.condition)

        # Should use template line (8%), not general (5%)
        self.assertAlmostEqual(line.seller_discount, 8.0, places=2)

    def test_condition_resolution_general_fallback(self):
        """Test that general seller_discount is used when no specific line."""
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_b, base_price=200.0)
        order._apply_condition_to_line(line, self.condition)

        # No specific line for product_b, should use condition.seller_discount (5%)
        self.assertAlmostEqual(line.seller_discount, 5.0, places=2)

    def test_no_policy_keeps_standard_behavior(self):
        """Test that lines without sales_profile use standard Odoo behavior."""
        # Create order without profile
        self.salesperson.partner_id.sales_profile_id = False
        # Clear company default so profile can't resolve via fallback
        self.env.company.default_sales_profile_id = False
        order = self._create_order()
        self.assertFalse(order.sales_profile_id)
        line = self._create_order_line(order)
        # price_unit should come from standard Odoo compute
        self.assertTrue(line.price_unit >= 0)

    def test_discount_fixed_set_on_apply_condition(self):
        """Test that discount_fixed is set to True when condition is applied."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        order._apply_condition_to_line(line, self.condition)
        self.assertTrue(line.discount_fixed)

    def test_pricelist_from_condition(self):
        """Test that pricelist is taken from commercial condition."""
        order = self._create_order()
        self.assertEqual(order.pricelist_id, self.pricelist)


@tagged("post_install", "-at_install")
class TestResolveDiscountForProduct(CommercialPolicyTestCommon):
    """Tests for _resolve_discount_for_product on partner.commercial.condition.

    Verifies the centralized resolution: variant > template > general.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_general_fallback(self):
        """Returns general seller_discount when no specific line exists."""
        seller, extra, level = self.condition._resolve_discount_for_product(
            self.product_b
        )
        self.assertAlmostEqual(seller, self.condition.seller_discount, places=2)
        self.assertAlmostEqual(extra, 0.0, places=2)
        self.assertEqual(level, "general")

    def test_template_over_general(self):
        """Template-specific line takes precedence over general."""
        self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "product_tmpl_id": self.product_template_b.id,
                "seller_discount": 8.0,
                "extra_discount": 0.5,
            }
        )
        seller, extra, level = self.condition._resolve_discount_for_product(
            self.product_b
        )
        self.assertAlmostEqual(seller, 8.0, places=2)
        self.assertAlmostEqual(extra, 0.5, places=2)
        self.assertEqual(level, "template")

    def test_variant_over_template(self):
        """Variant-specific line takes precedence over template."""
        self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "product_tmpl_id": self.product_template_a.id,
                "seller_discount": 3.0,
                "extra_discount": 1.0,
            }
        )
        self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product",
                "product_id": self.product_a.id,
                "seller_discount": 7.0,
                "extra_discount": 2.0,
            }
        )
        seller, extra, level = self.condition._resolve_discount_for_product(
            self.product_a
        )
        self.assertAlmostEqual(seller, 7.0, places=2)
        self.assertAlmostEqual(extra, 2.0, places=2)
        self.assertEqual(level, "variant")

    def test_apply_condition_uses_resolve(self):
        """_apply_condition_to_line applies exactly what _resolve returns."""
        self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "product_tmpl_id": self.product_template_b.id,
                "seller_discount": 6.0,
                "extra_discount": 1.5,
            }
        )
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_b)
        order._apply_condition_to_line(line, self.condition)

        expected = self.condition._resolve_discount_for_product(self.product_b)
        self.assertAlmostEqual(line.seller_discount, expected[0], places=2)
        self.assertAlmostEqual(line.extra_discount, expected[1], places=2)
        self.assertTrue(line.discount_fixed)

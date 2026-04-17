# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestEndToEndPricing(CommercialPolicyTestCommon):
    """End-to-end pricing tests covering the full chain:

    pricelist → base_price → reference_price (contractual return)
    → seller_discount + extra_discount → price_unit
    → cash_discount + fob_discount → discount (on line)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

        # Create a third product
        cls.product_template_c = cls.env["product.template"].create(
            {
                "name": "Product C",
                "type": "consu",
                "list_price": 500.0,
                "categ_id": cls.categ_chemicals.id,
            }
        )
        cls.product_c = cls.product_template_c.product_variant_ids[0]

        # Set pricelist items so base_price is predictable
        cls.env["product.pricelist.item"].create(
            {
                "pricelist_id": cls.pricelist.id,
                "applied_on": "1_product",
                "product_tmpl_id": cls.product_template_a.id,
                "compute_price": "fixed",
                "fixed_price": 100.0,
            }
        )
        cls.env["product.pricelist.item"].create(
            {
                "pricelist_id": cls.pricelist.id,
                "applied_on": "1_product",
                "product_tmpl_id": cls.product_template_b.id,
                "compute_price": "fixed",
                "fixed_price": 200.0,
            }
        )
        cls.env["product.pricelist.item"].create(
            {
                "pricelist_id": cls.pricelist.id,
                "applied_on": "1_product",
                "product_tmpl_id": cls.product_template_c.id,
                "compute_price": "fixed",
                "fixed_price": 500.0,
            }
        )

    def _create_line_with_condition(self, order, product=None, qty=1):
        """Create a sale order line and apply the commercial condition.

        Simulates the UI flow where _onchange_product_id_apply_condition
        runs after adding a product to a line.
        """
        line = self._create_order_line(order, product=product, qty=qty)
        order._apply_condition_to_line(line, order.commercial_condition_id)
        return line

    def test_full_chain_no_contractual_return(self):
        """Full pricing chain without contractual return.

        pricelist(100) → base_price(100) → reference_price(100)
        → seller_discount(5%) → price_unit(95)
        → cash(2%) + fob(1%) → discount(3%)
        """
        order = self._create_order()
        line = self._create_line_with_condition(order, product=self.product_a)

        # base_price comes from pricelist
        self.assertAlmostEqual(line.base_price, 100.0, places=2)

        # No contractual return → reference_price = base_price
        self.assertAlmostEqual(line.reference_price, 100.0, places=2)

        # seller_discount from condition general (5%)
        self.assertAlmostEqual(line.seller_discount, 5.0, places=2)

        # price_unit = reference_price * (1 - 5/100) = 95
        self.assertAlmostEqual(line.price_unit, 95.0, places=2)

        # cash(2%) + fob(1%) from condition → discount = 3%
        self.assertAlmostEqual(order.cash_discount, 2.0, places=2)
        self.assertAlmostEqual(order.fob_discount, 1.0, places=2)
        self.assertAlmostEqual(line.discount, 3.0, places=2)

    def test_full_chain_with_contractual_return(self):
        """Full pricing chain with contractual return adjusting reference price.

        pricelist(100) → base_price(100) → reference_price > 100
        → seller_discount(5%) → price_unit
        → cash(2%) + fob(1%) → discount(3%)
        """
        self.condition.with_user(self.director_user).contractual_return = 5.0

        order = self._create_order()
        line = self._create_line_with_condition(order, product=self.product_a)

        # base_price from pricelist
        self.assertAlmostEqual(line.base_price, 100.0, places=2)

        # With 5% contractual return, reference_price > base_price
        self.assertTrue(
            line.reference_price > line.base_price,
            "Reference price should be higher than base price with contractual return",
        )
        self.assertAlmostEqual(
            line.adjustment_factor, order.adjustment_factor, places=4
        )
        self.assertTrue(line.adjustment_factor > 0)

        # Verify reference_price = base_price * (1 + factor/100)
        expected_ref = line.base_price * (1 + line.adjustment_factor / 100)
        self.assertAlmostEqual(line.reference_price, expected_ref, places=2)

        # price_unit = reference_price * (1 - seller_discount/100)
        expected_price = line.reference_price * (1 - line.seller_discount / 100)
        self.assertAlmostEqual(line.price_unit, expected_price, places=2)

        # discount = cash + fob
        self.assertAlmostEqual(line.discount, 3.0, places=2)

    def test_full_chain_all_discounts_active(self):
        """All four discount types active simultaneously.

        seller_discount + extra_discount → price_unit
        cash_discount + fob_discount → discount
        """
        order = self._create_order()
        line = self._create_line_with_condition(order, product=self.product_a)

        # Apply extra discount
        line.write(
            {
                "extra_discount": 2.0,
                "extra_discount_reason": "Special negotiation",
            }
        )

        # seller_discount=5% (from condition) + extra_discount=2%
        self.assertAlmostEqual(line.seller_discount, 5.0, places=2)
        self.assertAlmostEqual(line.extra_discount, 2.0, places=2)

        # price_unit = reference_price * (1 - (5+2)/100) = 100 * 0.93 = 93
        expected_price = line.reference_price * (1 - 7.0 / 100)
        self.assertAlmostEqual(line.price_unit, expected_price, places=2)

        # discount = cash(2%) + fob(1%) = 3%
        self.assertAlmostEqual(line.discount, 3.0, places=2)

        # discount_value = qty * price_unit * discount / 100
        expected_disc_value = line.product_uom_qty * line.price_unit * 3.0 / 100
        self.assertAlmostEqual(line.discount_value, expected_disc_value, places=2)

    def test_full_chain_multiple_products_different_prices(self):
        """Multiple products in same order, each with different pricelist prices."""
        order = self._create_order()
        line_a = self._create_line_with_condition(order, product=self.product_a, qty=2)
        line_b = self._create_line_with_condition(order, product=self.product_b, qty=3)

        # Product A: base=100, Product B: base=200
        self.assertAlmostEqual(line_a.base_price, 100.0, places=2)
        self.assertAlmostEqual(line_b.base_price, 200.0, places=2)

        # Both get seller_discount=5% from general condition
        self.assertAlmostEqual(line_a.seller_discount, 5.0, places=2)
        self.assertAlmostEqual(line_b.seller_discount, 5.0, places=2)

        # price_unit = reference_price * (1 - 5/100)
        self.assertAlmostEqual(line_a.price_unit, 100.0 * 0.95, places=2)
        self.assertAlmostEqual(line_b.price_unit, 200.0 * 0.95, places=2)

    def test_full_chain_with_contractual_return_and_all_discounts(self):
        """Most complex scenario: contractual return + all four discounts."""
        self.condition.with_user(self.director_user).contractual_return = 5.0

        order = self._create_order()
        line = self._create_line_with_condition(order, product=self.product_c, qty=10)

        # base_price = 500 from pricelist
        self.assertAlmostEqual(line.base_price, 500.0, places=2)

        # reference_price adjusted by contractual return factor
        factor = line.adjustment_factor
        self.assertTrue(factor > 0)
        expected_ref = 500.0 * (1 + factor / 100)
        self.assertAlmostEqual(line.reference_price, expected_ref, places=2)

        # Apply extra discount
        line.write(
            {
                "extra_discount": 3.0,
                "extra_discount_reason": "Volume deal",
            }
        )

        # price_unit = reference_price * (1 - (seller_disc + extra_disc) / 100)
        total_disc = line.seller_discount + line.extra_discount
        expected_price = line.reference_price * (1 - total_disc / 100)
        self.assertAlmostEqual(line.price_unit, expected_price, places=2)

        # discount = cash + fob = 3%
        self.assertAlmostEqual(line.discount, 3.0, places=2)

        # Final effective price per unit (what customer actually pays)
        effective_price = line.price_unit * (1 - line.discount / 100)
        self.assertTrue(effective_price < 500.0)
        self.assertTrue(effective_price > 0)


@tagged("post_install", "-at_install")
class TestDiscountSpecificity(CommercialPolicyTestCommon):
    """Test discount resolution at all specificity levels.

    Condition discount resolution: variant > template > general.
    Profile rule resolution: variant > template > category > general.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

        # Add variant-level condition line for product_a
        cls.condition_line_variant = (
            cls.env["partner.commercial.condition.line"]
            .with_user(cls.salesperson)
            .create(
                {
                    "condition_id": cls.condition.id,
                    "applied_on": "product",
                    "product_id": cls.product_a.id,
                    "seller_discount": 3.0,
                }
            )
        )

        # Add template-level condition line for product_b's template
        cls.condition_line_template = (
            cls.env["partner.commercial.condition.line"]
            .with_user(cls.salesperson)
            .create(
                {
                    "condition_id": cls.condition.id,
                    "product_tmpl_id": cls.product_template_b.id,
                    "seller_discount": 4.0,
                }
            )
        )

        # Add variant-level profile rule for product_a
        cls.variant_rule = cls.env["tr.sales.profile.rule"].create(
            {
                "profile_id": cls.agent_profile.id,
                "applied_on": "product",
                "product_id": cls.product_a.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 8.0, "commission_rate": 12.0}),
                    (0, 0, {"discount_up_to": 15.0, "commission_rate": 5.0}),
                ],
            }
        )

        # Add template-level profile rule for product_b
        cls.template_rule = cls.env["tr.sales.profile.rule"].create(
            {
                "profile_id": cls.agent_profile.id,
                "applied_on": "product_template",
                "product_tmpl_id": cls.product_template_b.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 6.0, "commission_rate": 11.0}),
                    (0, 0, {"discount_up_to": 12.0, "commission_rate": 6.0}),
                ],
            }
        )

        # Add category-level profile rule for categ_chemicals
        cls.category_rule = cls.env["tr.sales.profile.rule"].create(
            {
                "profile_id": cls.agent_profile.id,
                "applied_on": "category",
                "categ_id": cls.categ_chemicals.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 7.0, "commission_rate": 9.0}),
                    (0, 0, {"discount_up_to": 14.0, "commission_rate": 4.0}),
                ],
            }
        )

        # Create product_d outside categ_chemicals (only general rule applies)
        cls.product_template_d = cls.env["product.template"].create(
            {
                "name": "Product D",
                "type": "consu",
                "list_price": 150.0,
                "categ_id": cls.env.ref("product.product_category_all").id,
            }
        )
        cls.product_d = cls.product_template_d.product_variant_ids[0]

    def _create_line_with_condition(self, order, product=None, qty=1):
        """Create line and apply commercial condition (simulates UI onchange)."""
        line = self._create_order_line(order, product=product, qty=qty)
        order._apply_condition_to_line(line, order.commercial_condition_id)
        return line

    def test_condition_variant_discount_applied(self):
        """Product A gets variant-level discount (3%) from condition."""
        order = self._create_order()
        line = self._create_line_with_condition(order, product=self.product_a)

        self.assertAlmostEqual(
            line.seller_discount,
            3.0,
            places=2,
            msg="Should use variant condition discount (3%), not general (5%)",
        )

    def test_condition_template_discount_applied(self):
        """Product B gets template-level discount (4%) from condition."""
        order = self._create_order()
        line = self._create_line_with_condition(order, product=self.product_b)

        self.assertAlmostEqual(
            line.seller_discount,
            4.0,
            places=2,
            msg="Should use template condition discount (4%), not general (5%)",
        )

    def test_condition_general_fallback(self):
        """Product D has no specific condition line, falls back to general (5%)."""
        order = self._create_order()
        line = self._create_line_with_condition(order, product=self.product_d)

        self.assertAlmostEqual(
            line.seller_discount,
            5.0,
            places=2,
            msg="Should use general condition discount (5%)",
        )

    def test_profile_rule_variant_over_template_and_category(self):
        """Product A: variant rule (max 15%) takes priority over category (14%)."""
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_a)

        self.assertAlmostEqual(line.seller_discount_max, 15.0, places=2)

    def test_profile_rule_template_over_category(self):
        """Product B: template rule (max 12%) takes priority over category.

        Product B is in categ_solvents (child of categ_chemicals).
        Template rule max=12%, category rule max=14%.
        Template wins because it's more specific.
        """
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_b)

        self.assertAlmostEqual(line.seller_discount_max, 12.0, places=2)

    def test_profile_rule_category_over_general(self):
        """Product in categ_chemicals (category rule 14%) beats general (10%)."""
        product_template_e = self.env["product.template"].create(
            {
                "name": "Product E",
                "type": "consu",
                "list_price": 300.0,
                "categ_id": self.categ_chemicals.id,
            }
        )
        product_e = product_template_e.product_variant_ids[0]

        order = self._create_order()
        line = self._create_order_line(order, product=product_e)

        self.assertAlmostEqual(
            line.seller_discount_max,
            14.0,
            places=2,
            msg="Should use category rule (14%), not general (10%)",
        )

    def test_profile_rule_general_fallback(self):
        """Product D (outside categ_chemicals): only general rule applies (max 10%)."""
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_d)

        self.assertAlmostEqual(line.seller_discount_max, 10.0, places=2)

    def test_profile_rule_child_category_walks_up(self):
        """Product in categ_solvents walks up to categ_chemicals rule (14%)."""
        product_template_f = self.env["product.template"].create(
            {
                "name": "Product F (Solvent)",
                "type": "consu",
                "list_price": 180.0,
                "categ_id": self.categ_solvents.id,
            }
        )
        product_f = product_template_f.product_variant_ids[0]

        order = self._create_order()
        line = self._create_order_line(order, product=product_f)

        self.assertAlmostEqual(
            line.seller_discount_max,
            14.0,
            places=2,
            msg="Should walk up from solvents to chemicals category rule (14%)",
        )

    def test_all_three_levels_coexist(self):
        """Three products on same order, each resolving at different level.

        Product A → variant condition (3%) + variant rule (max 15%)
        Product B → template condition (4%) + template rule (max 12%)
        Product D → general condition (5%) + general rule (max 10%)
        """
        order = self._create_order()
        line_a = self._create_line_with_condition(order, product=self.product_a)
        line_b = self._create_line_with_condition(order, product=self.product_b)
        line_d = self._create_line_with_condition(order, product=self.product_d)

        # Condition discounts at different levels
        self.assertAlmostEqual(line_a.seller_discount, 3.0, places=2)
        self.assertAlmostEqual(line_b.seller_discount, 4.0, places=2)
        self.assertAlmostEqual(line_d.seller_discount, 5.0, places=2)

        # Profile rule max limits at different levels
        self.assertAlmostEqual(line_a.seller_discount_max, 15.0, places=2)
        self.assertAlmostEqual(line_b.seller_discount_max, 12.0, places=2)
        self.assertAlmostEqual(line_d.seller_discount_max, 10.0, places=2)

        # Each line price_unit respects its own discount
        self.assertAlmostEqual(
            line_a.price_unit,
            line_a.reference_price * (1 - 3.0 / 100),
            places=2,
        )
        self.assertAlmostEqual(
            line_b.price_unit,
            line_b.reference_price * (1 - 4.0 / 100),
            places=2,
        )
        self.assertAlmostEqual(
            line_d.price_unit,
            line_d.reference_price * (1 - 5.0 / 100),
            places=2,
        )

    def test_commission_rate_follows_rule_specificity(self):
        """Commission rate resolved from the applicable rule's bands.

        Product A (variant rule): seller_discount=3% → band up_to 8% → rate 12%
        Product B (template rule): seller_discount=4% → band up_to 6% → rate 11%
        Product D (general rule): seller_discount=5% → band up_to 5% → rate 10%
        """
        order = self._create_order()
        line_a = self._create_line_with_condition(order, product=self.product_a)
        line_b = self._create_line_with_condition(order, product=self.product_b)
        line_d = self._create_line_with_condition(order, product=self.product_d)

        self.assertAlmostEqual(line_a.commission_rate, 12.0, places=2)
        self.assertAlmostEqual(line_b.commission_rate, 11.0, places=2)
        self.assertAlmostEqual(line_d.commission_rate, 10.0, places=2)

    def test_full_chain_variant_with_contractual_return_and_extra(self):
        """Variant-level + contractual return + extra discount + cash/fob."""
        self.condition.with_user(self.director_user).contractual_return = 5.0

        order = self._create_order()
        line = self._create_line_with_condition(order, product=self.product_a)

        # Variant discount (3%) is applied, not general (5%)
        self.assertAlmostEqual(line.seller_discount, 3.0, places=2)

        # Contractual return adjusts reference price upward
        self.assertTrue(line.reference_price > line.base_price)
        expected_ref = line.base_price * (1 + line.adjustment_factor / 100)
        self.assertAlmostEqual(line.reference_price, expected_ref, places=2)

        # Add extra discount
        line.write(
            {
                "extra_discount": 2.0,
                "extra_discount_reason": "Customer negotiation",
            }
        )

        # price_unit = reference_price * (1 - (3+2)/100)
        expected_price = line.reference_price * (1 - 5.0 / 100)
        self.assertAlmostEqual(line.price_unit, expected_price, places=2)

        # discount = cash(2%) + fob(1%) = 3%
        self.assertAlmostEqual(line.discount, 3.0, places=2)

        # Commission from variant rule: seller_discount=3% → band up_to 8% → 12%
        self.assertAlmostEqual(line.commission_rate, 12.0, places=2)

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests.common import Form

from .common import PricelistReportTestCommon


class TestPricingConsistency(PricelistReportTestCommon):
    """Invariant: printed price equals what sale.order.line would compute."""

    def _create_order_with_line(self, product):
        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "pricelist_id": self.pricelist.id,
            }
        )
        with Form(order) as form:
            with form.order_line.new() as line:
                line.product_id = product
                line.product_uom_qty = 1.0
        return order

    def _get_order_line_price(self, product):
        order = self._create_order_with_line(product)
        line = order.order_line[0]
        line._compute_base_price()
        line._compute_reference_price()
        line._compute_price_unit()
        return {
            "base": line.base_price,
            "reference": line.reference_price,
            "price_unit": line.price_unit,
        }

    def test_parity_template_product(self):
        """Wizard and sale.order.line return the same price_unit."""
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        from odoo.addons.tr_commercial_policy.models.policy_utils import (
            get_policy_rates,
        )

        rates = get_policy_rates(self.env)
        wizard_pricing = wizard._compute_pricing(self.product_a, rates)
        order_pricing = self._get_order_line_price(self.product_a)
        self.assertAlmostEqual(wizard_pricing["base"], order_pricing["base"], places=2)
        self.assertAlmostEqual(
            wizard_pricing["reference"], order_pricing["reference"], places=2
        )
        self.assertAlmostEqual(
            wizard_pricing["price_unit"], order_pricing["price_unit"], places=2
        )

    def test_base_price_reads_pricelist(self):
        """Base price reflects the pricelist's fixed_price for the template."""
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        base = wizard._compute_base_price(self.product_a)
        self.assertAlmostEqual(base, 100.0, places=2)

    def test_skip_adjustment_factor_makes_reference_equal_base(self):
        """Transition pricelist flag collapses reference_price into base."""
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        from odoo.addons.tr_commercial_policy.models.policy_utils import (
            get_policy_rates,
        )
        from unittest.mock import patch

        rates = get_policy_rates(self.env)
        with patch.object(
            type(wizard), "_pricelist_skip_adjustment", return_value=True
        ):
            pricing = wizard._compute_pricing(self.product_a, rates)
        self.assertAlmostEqual(pricing["reference"], pricing["base"], places=2)

    def test_block_discounts_forces_seller_to_zero(self):
        """Transition pricelist flag zeroes the seller discount."""
        # Give the condition a non-zero general seller discount so the
        # no-block branch would otherwise apply a discount.
        self.condition.seller_discount = 5.0
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        from odoo.addons.tr_commercial_policy.models.policy_utils import (
            get_policy_rates,
        )
        from unittest.mock import patch

        rates = get_policy_rates(self.env)
        with patch.object(
            type(wizard), "_pricelist_blocks_discounts", return_value=True
        ):
            pricing = wizard._compute_pricing(self.product_a, rates)
        self.assertEqual(pricing["seller_discount"], 0.0)

    def test_extra_discount_applied_to_price_unit(self):
        """Partner extra discount is applied on top of seller discount."""
        from odoo.addons.tr_commercial_policy.models.policy_utils import (
            calc_price_unit,
            get_policy_rates,
        )

        self.condition.with_user(self.manager_user).line_ids = [
            (
                0,
                0,
                {
                    "product_tmpl_id": self.product_template_a.id,
                    "applied_on": "product_template",
                    "seller_discount": 5.0,
                    "extra_discount": 3.0,
                },
            )
        ]
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        rates = get_policy_rates(self.env)
        pricing = wizard._compute_pricing(self.product_a, rates)
        expected = calc_price_unit(pricing["reference"], 5.0, 3.0)
        self.assertAlmostEqual(pricing["price_unit"], expected, places=2)
        # Parity with sale.order.line (which already applies extra).
        order_pricing = self._get_order_line_price(self.product_a)
        self.assertAlmostEqual(
            pricing["price_unit"], order_pricing["price_unit"], places=2
        )

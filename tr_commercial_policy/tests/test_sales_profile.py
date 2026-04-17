# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestSalesProfile(CommercialPolicyTestCommon):
    def test_create_profile_with_general_rule(self):
        """Test creating a profile with a general rule."""
        rule = self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": self.agent_profile.id,
                "applied_on": "general",
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 10.0, "commission_rate": 8.0}),
                ],
            }
        )
        # seller_discount_max computed from last band's discount_up_to
        self.assertEqual(rule.seller_discount_max, 10.0)
        self.assertIn(rule, self.agent_profile.rule_ids)

    def test_create_profile_with_product_rule(self):
        """Test creating a profile with a product variant rule."""
        rule = self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": self.agent_profile.id,
                "applied_on": "product",
                "product_id": self.product_a.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 15.0, "commission_rate": 8.0}),
                ],
            }
        )
        self.assertEqual(rule.product_id, self.product_a)

    def test_product_rule_requires_product(self):
        """Test that product rule requires product_id."""
        with self.assertRaises(ValidationError):
            self.env["tr.sales.profile.rule"].create(
                {
                    "profile_id": self.agent_profile.id,
                    "applied_on": "product",
                    "commission_band_ids": [
                        (0, 0, {"discount_up_to": 15.0, "commission_rate": 8.0}),
                    ],
                }
            )

    def test_template_rule_requires_template(self):
        """Test that product_template rule requires product_tmpl_id."""
        with self.assertRaises(ValidationError):
            self.env["tr.sales.profile.rule"].create(
                {
                    "profile_id": self.agent_profile.id,
                    "applied_on": "product_template",
                    "commission_band_ids": [
                        (0, 0, {"discount_up_to": 15.0, "commission_rate": 8.0}),
                    ],
                }
            )

    def test_category_rule_requires_category(self):
        """Test that category rule requires categ_id."""
        with self.assertRaises(ValidationError):
            self.env["tr.sales.profile.rule"].create(
                {
                    "profile_id": self.agent_profile.id,
                    "applied_on": "category",
                    "commission_band_ids": [
                        (0, 0, {"discount_up_to": 15.0, "commission_rate": 8.0}),
                    ],
                }
            )

    def test_commission_bands_strictly_decreasing(self):
        """Test that commission rates must be strictly decreasing."""
        rule = self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": self.agent_profile.id,
                "applied_on": "general",
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 5.0, "commission_rate": 10.0}),
                    (0, 0, {"discount_up_to": 10.0, "commission_rate": 8.0}),
                ],
            }
        )
        # This should fail: rate 9.0 is not less than 8.0
        with self.assertRaises(ValidationError):
            self.env["tr.sales.profile.commission.band"].create(
                {
                    "rule_id": rule.id,
                    "discount_up_to": 15.0,
                    "commission_rate": 9.0,
                }
            )

    def test_order_bands_strictly_increasing(self):
        """Test that seller_discount_max must be strictly increasing."""
        rule = self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": self.internal_profile.id,
                "applied_on": "general",
                "order_value_band_ids": [
                    (0, 0, {"order_min_amount": 1000.0, "seller_discount_max": 5.0}),
                    (0, 0, {"order_min_amount": 5000.0, "seller_discount_max": 10.0}),
                ],
            }
        )
        # This should fail: discount 8.0 is not greater than 10.0
        with self.assertRaises(ValidationError):
            self.env["tr.sales.profile.order.band"].create(
                {
                    "rule_id": rule.id,
                    "order_min_amount": 10000.0,
                    "seller_discount_max": 8.0,
                }
            )

    def test_agent_rule_requires_commission_bands(self):
        """Test that agent profile rules require at least one commission band."""
        with self.assertRaises(ValidationError):
            self.env["tr.sales.profile.rule"].create(
                {
                    "profile_id": self.agent_profile.id,
                    "applied_on": "general",
                }
            )

    def test_internal_rule_requires_order_bands(self):
        """Test that internal profile rules require at least one order value band."""
        with self.assertRaises(ValidationError):
            self.env["tr.sales.profile.rule"].create(
                {
                    "profile_id": self.internal_profile.id,
                    "applied_on": "general",
                }
            )

    def test_profile_required_on_confirm(self):
        """Test that confirming without a sales profile raises an error.

        The company default profile must be set so the policy guard
        considers this order governed (otherwise it skips validation).
        """
        self.customer.agent_ids = [(5,)]
        # Keep company default so _is_commercial_policy_applicable returns True
        self.env.company.default_sales_profile_id = self.agent_profile

        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "user_id": self.salesperson.id,
            }
        )
        self.assertFalse(order.sales_profile_id)
        with self.assertRaises(UserError):
            order.action_confirm()

    def test_seller_discount_max_resolution_order(self):
        """Test discount max resolution: product > template > category > general."""
        # Create rules at different levels (bands set seller_discount_max)
        self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": self.agent_profile.id,
                "applied_on": "general",
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 5.0, "commission_rate": 8.0}),
                ],
            }
        )
        self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": self.agent_profile.id,
                "applied_on": "category",
                "categ_id": self.categ_chemicals.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 8.0, "commission_rate": 8.0}),
                ],
            }
        )
        self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": self.agent_profile.id,
                "applied_on": "product_template",
                "product_tmpl_id": self.product_template_a.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 12.0, "commission_rate": 8.0}),
                ],
            }
        )
        self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": self.agent_profile.id,
                "applied_on": "product",
                "product_id": self.product_a.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 15.0, "commission_rate": 8.0}),
                ],
            }
        )

        # Profile now resolves from condition (agent → company default).
        # Set company default so condition resolves to agent_profile.
        self.env.company.default_sales_profile_id = self.agent_profile
        condition = self.env["partner.commercial.condition"].create(
            {"partner_id": self.customer.id, "pricelist_id": self.pricelist.id}
        )
        self.customer.commercial_condition_id = condition

        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "user_id": self.salesperson.id,
            }
        )

        # Product A: has variant rule → 15%
        line_a = self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": self.product_a.id,
                "product_uom_qty": 1,
            }
        )
        self.assertEqual(line_a.seller_discount_max, 15.0)

        # Product B: no variant/template rule, but Solvents category
        # inherits from Chemicals category → 8%
        line_b = self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": self.product_b.id,
                "product_uom_qty": 1,
            }
        )
        self.assertEqual(line_b.seller_discount_max, 8.0)

    def test_profile_create_without_rules_raises(self):
        """Creating an agent profile without rules raises ValidationError.

        The constraint guarantees no profile can leave commissions
        unmanaged: every profile must have at least one rule.
        """
        with self.assertRaises(ValidationError):
            self.env["tr.sales.profile"].create(
                {
                    "name": "Profile Without Rules",
                    "profile_type": "agent",
                }
            )

    def test_internal_profile_create_without_rules_raises(self):
        """Creating an internal profile without rules raises ValidationError."""
        with self.assertRaises(ValidationError):
            self.env["tr.sales.profile"].create(
                {
                    "name": "Internal Without Rules",
                    "profile_type": "internal",
                }
            )

    def test_profile_write_clearing_rules_raises(self):
        """Clearing rule_ids on an existing profile raises ValidationError."""
        profile = self.env["tr.sales.profile"].create(
            {
                "name": "Profile A",
                "profile_type": "agent",
                "rule_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "general",
                            "commission_band_ids": [
                                (
                                    0,
                                    0,
                                    {"discount_up_to": 0.0, "commission_rate": 0.0},
                                ),
                            ],
                        },
                    ),
                ],
            }
        )
        with self.assertRaises(ValidationError):
            profile.write({"rule_ids": [(5, 0, 0)]})

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import Form, tagged

from ..models.policy_utils import (
    calc_adjustment_factor,
    calc_line_pricing,
    get_policy_rates,
)
from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestCommercialCondition(CommercialPolicyTestCommon):
    def test_create_condition(self):
        """Test creating a commercial condition."""
        condition = self.env["partner.commercial.condition"].create(
            {
                "partner_id": self.customer.id,
                "contractual_return": 3.0,
                "cash_discount": 2.0,
                "fob_discount": 1.5,
                "seller_discount": 5.0,
            }
        )
        self.assertEqual(condition.contractual_return, 3.0)
        self.assertEqual(condition.cash_discount, 2.0)

    def test_unique_condition_per_partner(self):
        """Test that only one condition per partner is allowed."""
        self.env["partner.commercial.condition"].create(
            {
                "partner_id": self.customer.id,
                "contractual_return": 3.0,
            }
        )
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self.env["partner.commercial.condition"].create(
                {
                    "partner_id": self.customer.id,
                    "contractual_return": 5.0,
                }
            )

    def test_duplicate_condition_blocked_by_unique_constraint(self):
        """Duplicating a condition fails due to unique (partner, company)."""
        condition = self.env["partner.commercial.condition"].create(
            {
                "partner_id": self.customer.id,
                "contractual_return": 3.0,
            }
        )
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            condition.copy()

    def test_contractual_return_validation(self):
        """Test that contractual return must be 0-100."""
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition"].create(
                {
                    "partner_id": self.customer.id,
                    "contractual_return": -1.0,
                }
            )

    def test_condition_line_unique_product(self):
        """Test unique constraint on condition lines."""
        condition = self.env["partner.commercial.condition"].create(
            {
                "partner_id": self.customer.id,
            }
        )
        self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": condition.id,
                "applied_on": "product",
                "product_id": self.product_a.id,
                "seller_discount": 5.0,
            }
        )
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition.line"].create(
                {
                    "condition_id": condition.id,
                    "applied_on": "product",
                    "product_id": self.product_a.id,
                    "seller_discount": 8.0,
                }
            )

    def test_condition_line_applied_on_constraint(self):
        """Test that applied_on and product fields must be consistent."""
        condition = self.env["partner.commercial.condition"].create(
            {"partner_id": self.customer.id}
        )
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition.line"].create(
                {
                    "condition_id": condition.id,
                    "applied_on": "product_template",
                    "product_tmpl_id": self.product_template_a.id,
                    "product_id": self.product_a.id,
                    "seller_discount": 5.0,
                }
            )
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition.line"].create(
                {
                    "condition_id": condition.id,
                    "applied_on": "product",
                    "product_id": self.product_a.id,
                    "product_tmpl_id": self.product_template_a.id,
                    "seller_discount": 5.0,
                }
            )

    def test_onchange_applied_on_clears_product_id(self):
        """Switching to product_template clears product_id via Form."""
        condition = self.env["partner.commercial.condition"].create(
            {"partner_id": self.customer.id}
        )
        with Form(
            self.env["partner.commercial.condition.line"].with_context(
                default_condition_id=condition.id,
            )
        ) as line_form:
            line_form.applied_on = "product"
            line_form.product_id = self.product_a
            line_form.seller_discount = 5.0
            line_form.applied_on = "product_template"
            self.assertFalse(line_form.product_id)

    def test_onchange_applied_on_clears_product_tmpl_id(self):
        """Switching to product clears product_tmpl_id via Form."""
        condition = self.env["partner.commercial.condition"].create(
            {"partner_id": self.customer.id}
        )
        with Form(
            self.env["partner.commercial.condition.line"].with_context(
                default_condition_id=condition.id,
            )
        ) as line_form:
            line_form.applied_on = "product_template"
            line_form.product_tmpl_id = self.product_template_a
            line_form.seller_discount = 5.0
            line_form.applied_on = "product"
            self.assertFalse(line_form.product_tmpl_id)

    def test_condition_resolution_direct(self):
        """Test that direct condition on partner is resolved."""
        condition = self.env["partner.commercial.condition"].create(
            {
                "partner_id": self.customer.id,
                "cash_discount": 2.0,
                "fob_discount": 1.0,
            }
        )
        self.customer.commercial_condition_id = condition

        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
            }
        )
        self.assertEqual(order.commercial_condition_id, condition)
        self.assertEqual(order.cash_discount, 2.0)
        self.assertEqual(order.fob_discount, 1.0)

    def test_condition_resolution_group_fallback(self):
        """Test that group condition is used when partner has none."""
        group_condition = self.env["partner.commercial.condition"].create(
            {
                "partner_id": self.customer_group.id,
                "cash_discount": 3.0,
                "fob_discount": 2.0,
            }
        )
        self.customer_group.commercial_condition_id = group_condition
        self.customer.company_group_id = self.customer_group

        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
            }
        )
        self.assertEqual(order.commercial_condition_id, group_condition)
        self.assertEqual(order.cash_discount, 3.0)

    def test_reference_price_no_return(self):
        """Test reference price equals base price when no contractual return."""
        self.salesperson.partner_id.sales_profile_id = self.agent_profile

        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "user_id": self.salesperson.id,
            }
        )
        line = self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": self.product_a.id,
                "product_uom_qty": 1,
                "base_price": 100.0,
            }
        )
        self.assertAlmostEqual(line.reference_price, 100.0, places=2)

    def test_reference_price_with_return(self):
        """Test reference price is adjusted by contractual return."""
        condition = self.env["partner.commercial.condition"].create(
            {
                "partner_id": self.customer.id,
                "contractual_return": 3.0,
            }
        )
        self.customer.commercial_condition_id = condition

        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
            }
        )
        line = self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": self.product_a.id,
                "product_uom_qty": 1,
                "base_price": 100.0,
            }
        )
        # Adjustment factor formula composes tax + freight + admin
        # additively: ref = base * (1 - total) / (1 - cr - total)
        # total = 0.10 + 0.05 + 0.02 = 0.17
        # ref = 100 * 0.83 / 0.80
        expected = (
            100.0 * (1 - 0.10 - 0.05 - 0.02) / (1 - 0.03 - 0.10 - 0.05 - 0.02)
        )
        self.assertAlmostEqual(line.reference_price, expected, places=2)

    def test_sales_profile_company_dependent(self):
        """Test that sales_profile_id on partner varies by company."""
        company_2 = self.env["res.company"].create({"name": "Company 2"})
        profile_2 = self.env["tr.sales.profile"].create(
            {
                "name": "Profile Company 2",
                "profile_type": "agent",
                "company_id": company_2.id,
                "rule_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "general",
                            "commission_band_ids": [
                                (0, 0, {"discount_up_to": 0.0, "commission_rate": 0.0}),
                            ],
                        },
                    ),
                ],
            }
        )
        # Assign profile for main company
        self.salesperson.partner_id.sales_profile_id = self.agent_profile
        self.assertEqual(
            self.salesperson.partner_id.sales_profile_id, self.agent_profile
        )
        # Assign different profile for company 2
        self.salesperson.partner_id.with_company(company_2).sales_profile_id = profile_2
        # Main company still sees original profile
        self.assertEqual(
            self.salesperson.partner_id.sales_profile_id, self.agent_profile
        )
        # Company 2 sees its own profile
        self.assertEqual(
            self.salesperson.partner_id.with_company(company_2).sales_profile_id,
            profile_2,
        )

    def test_commercial_condition_company_dependent(self):
        """Test that commercial_condition_id on partner varies by company."""
        company_2 = self.env["res.company"].create({"name": "Company 2"})
        condition_1 = self.env["partner.commercial.condition"].create(
            {
                "partner_id": self.customer.id,
                "cash_discount": 2.0,
            }
        )
        condition_2 = self.env["partner.commercial.condition"].create(
            {
                "partner_id": self.customer.id,
                "company_id": company_2.id,
                "cash_discount": 5.0,
            }
        )
        # Assign condition for main company
        self.customer.commercial_condition_id = condition_1
        # Assign different condition for company 2
        self.customer.with_company(company_2).commercial_condition_id = condition_2
        # Main company sees condition_1
        self.assertEqual(self.customer.commercial_condition_id, condition_1)
        # Company 2 sees condition_2
        self.assertEqual(
            self.customer.with_company(company_2).commercial_condition_id,
            condition_2,
        )

    def test_condition_inherits_mail_thread(self):
        """Test that commercial condition inherits mail.thread."""
        condition = self.env["partner.commercial.condition"].create(
            {
                "partner_id": self.customer.id,
                "contractual_return": 3.0,
            }
        )
        self.assertTrue(
            hasattr(condition, "message_ids"),
            "Commercial condition should inherit mail.thread",
        )
        self.assertTrue(
            hasattr(condition, "activity_ids"),
            "Commercial condition should inherit mail.activity.mixin",
        )
        # Verify field tracking is configured at class level
        model_cls = type(condition)
        field_obj = model_cls._fields.get("contractual_return")
        self.assertTrue(
            field_obj and field_obj.tracking,
            "contractual_return field should have tracking=True",
        )


@tagged("post_install", "-at_install")
class TestAdjustmentFactor(CommercialPolicyTestCommon):
    """Test adjustment_factor computation across models and policy_utils."""

    def test_calc_adjustment_factor_zero_return(self):
        """calc_adjustment_factor returns 0 when contractual_return is 0."""
        self.assertEqual(calc_adjustment_factor(0.0, 0.10, 0.05, 0.02), 0.0)

    def test_calc_adjustment_factor_negative_return(self):
        """calc_adjustment_factor returns 0 for negative contractual_return."""
        self.assertEqual(calc_adjustment_factor(-5.0, 0.10, 0.05, 0.02), 0.0)

    def test_calc_adjustment_factor_denominator_lte_zero(self):
        """calc_adjustment_factor returns 0 when denominator <= 0."""
        # denominator = 1 - 0.90 - 0.10 - 0.05 - 0.02 = -0.07 < 0
        self.assertEqual(calc_adjustment_factor(90.0, 0.10, 0.05, 0.02), 0.0)

    def test_calc_adjustment_factor_positive_return(self):
        """calc_adjustment_factor composes tax + freight + admin additively."""
        # total_rate = 0.10 + 0.05 + 0.02 = 0.17
        # num = 1 - 0.17 = 0.83
        # den = 1 - 0.03 - 0.17 = 0.80
        factor = calc_adjustment_factor(3.0, 0.10, 0.05, 0.02)
        expected = (0.83 / 0.80 - 1) * 100
        self.assertAlmostEqual(factor, expected, places=4)

    def test_calc_adjustment_factor_without_admin(self):
        """admin_rate = 0 falls back to legacy two-rate behavior."""
        # num = 1 - 0.10 - 0.05 = 0.85
        # den = 1 - 0.03 - 0.10 - 0.05 = 0.82
        factor = calc_adjustment_factor(3.0, 0.10, 0.05, 0.0)
        expected = (0.85 / 0.82 - 1) * 100
        self.assertAlmostEqual(factor, expected, places=4)

    def test_get_policy_rates_from_setup(self):
        """get_policy_rates reads the values configured in common.setUpClass."""
        tax_rate, freight_rate, admin_rate = get_policy_rates(self.env)
        self.assertAlmostEqual(tax_rate, 0.10, places=4)
        self.assertAlmostEqual(freight_rate, 0.05, places=4)
        self.assertAlmostEqual(admin_rate, 0.02, places=4)

    def test_get_policy_rates_fallback_when_unset(self):
        """get_policy_rates falls back to 0.0 when config params are unset."""
        icp = self.env["ir.config_parameter"].sudo()
        for key in (
            "tr_commercial_policy.tax_rate_pct",
            "tr_commercial_policy.freight_rate_pct",
            "tr_commercial_policy.admin_rate_pct",
        ):
            icp.search([("key", "=", key)]).unlink()
        tax_rate, freight_rate, admin_rate = get_policy_rates(self.env)
        self.assertAlmostEqual(tax_rate, 0.0, places=4)
        self.assertAlmostEqual(freight_rate, 0.0, places=4)
        self.assertAlmostEqual(admin_rate, 0.0, places=4)

    def test_get_policy_rates_custom(self):
        """get_policy_rates reads custom values from system parameters."""
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("tr_commercial_policy.tax_rate_pct", "20.0")
        icp.set_param("tr_commercial_policy.freight_rate_pct", "8.0")
        icp.set_param("tr_commercial_policy.admin_rate_pct", "3.5")
        tax_rate, freight_rate, admin_rate = get_policy_rates(self.env)
        self.assertAlmostEqual(tax_rate, 0.20, places=4)
        self.assertAlmostEqual(freight_rate, 0.08, places=4)
        self.assertAlmostEqual(admin_rate, 0.035, places=4)

    def test_settings_round_trip_policy_rates(self):
        """res.config.settings persists rates as percentage in config_parameter."""
        settings = self.env["res.config.settings"].create(
            {
                "tr_policy_tax_rate": 19.5,
                "tr_policy_freight_rate": 4.0,
                "tr_policy_admin_rate": 1.5,
            }
        )
        settings.execute()
        icp = self.env["ir.config_parameter"].sudo()
        self.assertAlmostEqual(
            float(icp.get_param("tr_commercial_policy.tax_rate_pct")),
            19.5,
            places=4,
        )
        self.assertAlmostEqual(
            float(icp.get_param("tr_commercial_policy.freight_rate_pct")),
            4.0,
            places=4,
        )
        self.assertAlmostEqual(
            float(icp.get_param("tr_commercial_policy.admin_rate_pct")),
            1.5,
            places=4,
        )
        # get_policy_rates must return the new values as decimal
        tax, freight, admin = get_policy_rates(self.env)
        self.assertAlmostEqual(tax, 0.195, places=4)
        self.assertAlmostEqual(freight, 0.04, places=4)
        self.assertAlmostEqual(admin, 0.015, places=4)

    def test_condition_adjustment_factor(self):
        """Condition computes adjustment_factor from contractual_return."""
        condition = self.env["partner.commercial.condition"].create(
            {
                "partner_id": self.customer.id,
                "contractual_return": 3.0,
            }
        )
        expected = calc_adjustment_factor(3.0, 0.10, 0.05, 0.02)
        self.assertAlmostEqual(condition.adjustment_factor, expected, places=4)

    def test_condition_adjustment_factor_zero(self):
        """Condition adjustment_factor is 0 when no contractual_return."""
        condition = self.env["partner.commercial.condition"].create(
            {
                "partner_id": self.customer.id,
            }
        )
        self.assertEqual(condition.adjustment_factor, 0.0)

    def test_order_adjustment_factor(self):
        """Sale order computes adjustment_factor from contractual_return."""
        condition = self.env["partner.commercial.condition"].create(
            {
                "partner_id": self.customer.id,
                "contractual_return": 3.0,
            }
        )
        self.customer.commercial_condition_id = condition
        order = self.env["sale.order"].create({"partner_id": self.customer.id})
        expected = calc_adjustment_factor(3.0, 0.10, 0.05, 0.02)
        self.assertAlmostEqual(order.adjustment_factor, expected, places=4)

    def test_order_adjustment_factor_zero(self):
        """Sale order adjustment_factor is 0 without contractual_return."""
        order = self.env["sale.order"].create({"partner_id": self.customer.id})
        self.assertEqual(order.adjustment_factor, 0.0)

    def test_line_adjustment_factor(self):
        """Sale order line computes adjustment_factor via _compute_reference_price."""
        condition = self.env["partner.commercial.condition"].create(
            {
                "partner_id": self.customer.id,
                "contractual_return": 3.0,
            }
        )
        self.customer.commercial_condition_id = condition
        order = self.env["sale.order"].create({"partner_id": self.customer.id})
        line = self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": self.product_a.id,
                "product_uom_qty": 1,
                "base_price": 100.0,
            }
        )
        expected_factor = calc_adjustment_factor(3.0, 0.10, 0.05, 0.02)
        self.assertAlmostEqual(line.adjustment_factor, expected_factor, places=4)
        # reference_price = base * (1 + factor / 100)
        expected_ref = 100.0 * (1 + expected_factor / 100)
        self.assertAlmostEqual(line.reference_price, expected_ref, places=2)

    def test_line_adjustment_factor_zero_base(self):
        """Line with base_price=0 gets reference_price=0 regardless of factor."""
        condition = self.env["partner.commercial.condition"].create(
            {
                "partner_id": self.customer.id,
                "contractual_return": 3.0,
            }
        )
        self.customer.commercial_condition_id = condition
        order = self.env["sale.order"].create({"partner_id": self.customer.id})
        line = self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": self.product_a.id,
                "product_uom_qty": 1,
                "base_price": 0.0,
            }
        )
        self.assertEqual(line.reference_price, 0.0)


@tagged("post_install", "-at_install")
class TestCalcLinePricing(CommercialPolicyTestCommon):
    """Unit tests for calc_line_pricing — pure numerical composition."""

    def test_full_chain_no_return(self):
        """No contractual return: reference = base, price_unit applies discounts."""
        result = calc_line_pricing(
            base_price=100.0,
            contractual_return=0.0,
            seller_discount=5.0,
            extra_discount=2.0,
            cash_discount=3.0,
            fob_discount=1.0,
            qty=10,
            tax_rate=0.10,
            freight_rate=0.05,
            admin_rate=0.02,
        )
        self.assertAlmostEqual(result["adjustment_factor"], 0.0, places=4)
        self.assertAlmostEqual(result["reference_price"], 100.0, places=2)
        self.assertAlmostEqual(result["price_unit"], 93.0, places=2)
        self.assertAlmostEqual(result["discount"], 4.0, places=2)
        self.assertAlmostEqual(
            result["discount_value"], 10 * 93.0 * 4.0 / 100, places=2
        )

    def test_full_chain_with_return(self):
        """Contractual return adjusts reference price upward."""
        result = calc_line_pricing(
            base_price=100.0,
            contractual_return=3.0,
            seller_discount=5.0,
            extra_discount=0.0,
            cash_discount=2.0,
            fob_discount=1.0,
            qty=1,
            tax_rate=0.10,
            freight_rate=0.05,
            admin_rate=0.02,
        )
        self.assertTrue(result["adjustment_factor"] > 0)
        self.assertTrue(result["reference_price"] > 100.0)
        expected_price = result["reference_price"] * (1 - 5.0 / 100)
        self.assertAlmostEqual(result["price_unit"], expected_price, places=2)
        self.assertAlmostEqual(result["discount"], 3.0, places=2)

    def test_zero_base_price(self):
        """Zero base price yields all zeros."""
        result = calc_line_pricing(
            base_price=0.0,
            contractual_return=5.0,
            seller_discount=10.0,
            extra_discount=2.0,
            cash_discount=3.0,
            fob_discount=1.0,
            qty=5,
            tax_rate=0.10,
            freight_rate=0.05,
            admin_rate=0.02,
        )
        self.assertAlmostEqual(result["reference_price"], 0.0, places=2)
        self.assertAlmostEqual(result["price_unit"], 0.0, places=2)
        self.assertAlmostEqual(result["discount_value"], 0.0, places=2)

    def test_no_discounts(self):
        """No discounts: price_unit = reference_price, discount = 0."""
        result = calc_line_pricing(
            base_price=200.0,
            contractual_return=0.0,
            seller_discount=0.0,
            extra_discount=0.0,
            cash_discount=0.0,
            fob_discount=0.0,
            qty=1,
            tax_rate=0.10,
            freight_rate=0.05,
            admin_rate=0.02,
        )
        self.assertAlmostEqual(result["price_unit"], 200.0, places=2)
        self.assertAlmostEqual(result["discount"], 0.0, places=2)
        self.assertAlmostEqual(result["discount_value"], 0.0, places=2)


@tagged("post_install", "-at_install")
class TestConditionDiscountValidation(CommercialPolicyTestCommon):
    """Profile-based validation on condition and condition line discounts."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_director_bypasses_discount_validation(self):
        """Director bypasses _validate_discount_limits completely."""
        self.condition.with_user(self.director_user).write({"cash_discount": 99.0})
        self.assertAlmostEqual(self.condition.cash_discount, 99.0, places=2)

    def test_seller_discount_ok_when_no_general_rule(self):
        """Seller discount validation skips when profile has no general rule."""
        self.general_rule.unlink()
        self.condition.with_user(self.salesperson).write({"seller_discount": 50.0})
        self.assertAlmostEqual(self.condition.seller_discount, 50.0, places=2)

    def test_variant_rule_matched_on_condition_line_create(self):
        """Condition line for product_a uses variant rule max (8%)."""
        variant_rule = self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": self.agent_profile.id,
                "applied_on": "product",
                "product_id": self.product_a.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 8.0, "commission_rate": 6.0}),
                ],
            }
        )
        self.assertTrue(variant_rule)
        line = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.salesperson)
            .create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product",
                    "product_id": self.product_a.id,
                    "seller_discount": 7.0,
                }
            )
        )
        self.assertAlmostEqual(line.seller_discount, 7.0, places=2)

    def test_variant_rule_blocked_above_max(self):
        """Condition line above variant rule max (8%) is blocked."""
        self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": self.agent_profile.id,
                "applied_on": "product",
                "product_id": self.product_a.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 8.0, "commission_rate": 6.0}),
                ],
            }
        )
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition.line"].with_user(
                self.salesperson
            ).create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product",
                    "product_id": self.product_a.id,
                    "seller_discount": 9.0,
                }
            )

    def test_template_rule_matched_via_product_id(self):
        """When product_id given but no variant rule, template rule is used."""
        self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": self.agent_profile.id,
                "applied_on": "product_template",
                "product_tmpl_id": self.product_template_b.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 6.0, "commission_rate": 4.0}),
                ],
            }
        )
        line = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.salesperson)
            .create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product",
                    "product_id": self.product_b.id,
                    "seller_discount": 5.0,
                }
            )
        )
        self.assertAlmostEqual(line.seller_discount, 5.0, places=2)

    def test_director_bypasses_line_validation(self):
        """Director bypasses _validate_line_discount_limits."""
        line = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.director_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "product_tmpl_id": self.product_template_b.id,
                    "seller_discount": 99.0,
                    "extra_discount": 10.0,
                }
            )
        )
        self.assertAlmostEqual(line.seller_discount, 99.0, places=2)

    def test_no_profile_user_blocked_on_line_seller_discount(self):
        """User without profile gets AccessError writing seller_discount."""
        # Clear company default so profile can't resolve via fallback
        self.env.company.default_sales_profile_id = False
        # Also remove agent from customer so profile can't resolve from agent
        self.customer.agent_ids = [(5,)]
        user_no_profile = self.env["res.users"].create(
            {
                "name": "No Profile Cond Line",
                "login": "no_profile_cond_line_tcp",
                "groups_id": [
                    (4, self.env.ref("sales_team.group_sale_salesman").id),
                    (
                        4,
                        self.env.ref("tr_commercial_policy.group_sales_manager").id,
                    ),
                ],
            }
        )
        with self.assertRaises(AccessError):
            self.env["partner.commercial.condition.line"].with_user(
                user_no_profile
            ).create(
                {
                    "condition_id": self.condition.id,
                    "product_tmpl_id": self.product_template_b.id,
                    "seller_discount": 1.0,
                }
            )

    def test_write_seller_discount_on_existing_line(self):
        """Writing seller_discount on existing line triggers validation."""
        line = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.director_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "product_tmpl_id": self.product_template_b.id,
                    "seller_discount": 3.0,
                }
            )
        )
        line.with_user(self.salesperson).write({"seller_discount": 5.0})
        self.assertAlmostEqual(line.seller_discount, 5.0, places=2)

    def test_duplicate_variant_line_raises(self):
        """Creating a second variant-level line for same product raises."""
        self.env["partner.commercial.condition.line"].with_user(
            self.director_user
        ).create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product",
                "product_id": self.product_a.id,
                "seller_discount": 3.0,
            }
        )
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition.line"].with_user(
                self.director_user
            ).create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product",
                    "product_id": self.product_a.id,
                    "seller_discount": 5.0,
                }
            )

    def test_internal_empty_bands_blocks_any_discount(self):
        """Internal profile with no matching bands (avg=0) blocks seller_discount."""
        self._setup_internal_policy()
        # Profile now resolves from condition's partner (agent → company default).
        # Set company default to internal profile so validation uses order value bands.
        self.env.company.default_sales_profile_id = self.internal_profile
        # New customer with no order history → avg = 0
        # All bands have order_min_amount > 0 → no band matches → max = 0.0
        new_partner = self.env["res.partner"].create({"name": "No History"})
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition"].with_user(self.salesperson).create(
                {
                    "partner_id": new_partner.id,
                    "seller_discount": 1.0,
                }
            )

    def test_resolve_rule_tmpl_only_no_product_id(self):
        """Line with product_tmpl_id but no product_id uses template rule."""
        tmpl_rule = self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": self.agent_profile.id,
                "applied_on": "product_template",
                "product_tmpl_id": self.product_template_b.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 6.0, "commission_rate": 4.0}),
                ],
            }
        )
        CondLine = self.env["partner.commercial.condition.line"]
        rule = CondLine._resolve_rule_for_product(
            self.agent_profile,
            product_id=False,
            product_tmpl_id=self.product_template_b.id,
        )
        self.assertEqual(rule, tmpl_rule)

    def test_resolve_rule_no_general_returns_false(self):
        """When no general rule exists, _resolve_rule_for_product returns False."""
        self.general_rule.unlink()
        CondLine = self.env["partner.commercial.condition.line"]
        rule = CondLine._resolve_rule_for_product(
            self.agent_profile,
            product_id=False,
            product_tmpl_id=False,
        )
        self.assertFalse(rule)


@tagged("post_install", "-at_install")
class TestValidatePricelist(CommercialPolicyTestCommon):
    """Coverage for _validate_pricelist on condition create/write."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_pricelist_mismatch_raises_validation_error(self):
        """Non-director user cannot set pricelist that differs from profile."""
        other_pricelist = self.env["product.pricelist"].create(
            {"name": "Other PL", "currency_id": self.env.ref("base.BRL").id}
        )
        with self.assertRaises(ValidationError):
            self.condition.with_user(self.salesperson).write(
                {"pricelist_id": other_pricelist.id}
            )

    def test_director_bypasses_pricelist_validation(self):
        """Director can set any pricelist regardless of profile."""
        other_pricelist = self.env["product.pricelist"].create(
            {"name": "Other PL", "currency_id": self.env.ref("base.BRL").id}
        )
        self.condition.with_user(self.director_user).write(
            {"pricelist_id": other_pricelist.id}
        )
        self.assertEqual(self.condition.pricelist_id, other_pricelist)


@tagged("post_install", "-at_install")
class TestCreateUsesAgentProfileNotCompanyDefault(CommercialPolicyTestCommon):
    """Prove that create() validates against the partner's agent profile,
    not the company default — the bug that was fixed.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

        # Strict agent profile: low limits
        cls.strict_profile = cls.env["tr.sales.profile"].create(
            {
                "name": "Strict Agent Profile",
                "profile_type": "agent",
                "pricelist_ids": [(6, 0, [cls.pricelist.id])],
                "cash_discount_max": 2.0,
                "fob_discount_max": 1.0,
                "cash_term_avg_days_max": 15,
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
                                    {"discount_up_to": 3.0, "commission_rate": 10.0},
                                ),
                            ],
                        },
                    ),
                ],
            }
        )

        # Permissive company default: high limits
        cls.permissive_profile = cls.env["tr.sales.profile"].create(
            {
                "name": "Permissive Default",
                "profile_type": "agent",
                "pricelist_ids": [(6, 0, [cls.pricelist.id])],
                "cash_discount_max": 20.0,
                "fob_discount_max": 15.0,
                "cash_term_avg_days_max": 90,
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
                                    {"discount_up_to": 20.0, "commission_rate": 5.0},
                                ),
                            ],
                        },
                    ),
                ],
            }
        )
        cls.env.company.default_sales_profile_id = cls.permissive_profile

        # Agent on partner with strict profile
        commission = cls.env["commission"].create(
            {"name": "Test Comm", "commission_type": "fixed", "fix_qty": 10.0}
        )
        cls.strict_agent = cls.env["res.partner"].create(
            {
                "name": "Strict Agent",
                "agent": True,
                "commission_id": commission.id,
                "sales_profile_id": cls.strict_profile.id,
            }
        )
        # New partner with agent
        cls.partner_with_agent = cls.env["res.partner"].create(
            {"name": "Partner With Strict Agent"}
        )
        cls.partner_with_agent.agent_ids = [(4, cls.strict_agent.id)]

    def test_condition_create_uses_agent_profile_for_discount(self):
        """Create validates cash_discount against agent profile (2%), not
        company default (20%). Value of 5% should be blocked.
        """
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition"].with_user(self.salesperson).create(
                {
                    "partner_id": self.partner_with_agent.id,
                    "cash_discount": 5.0,
                }
            )

    def test_condition_create_uses_agent_profile_for_pricelist(self):
        """Create validates pricelist against agent profile's allowed list,
        not company default's.
        """
        other_pricelist = self.env["product.pricelist"].create(
            {"name": "Not Allowed PL", "currency_id": self.env.ref("base.BRL").id}
        )
        # Add to permissive (company default) but NOT to strict (agent)
        self.permissive_profile.pricelist_ids = [
            (4, other_pricelist.id),
        ]
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition"].with_user(self.salesperson).create(
                {
                    "partner_id": self.partner_with_agent.id,
                    "pricelist_id": other_pricelist.id,
                }
            )

    def test_condition_line_create_uses_agent_profile(self):
        """Condition line create validates seller_discount against the
        condition's partner agent profile (max 3%), not company default.
        """
        condition = (
            self.env["partner.commercial.condition"]
            .with_user(self.director_user)
            .create({"partner_id": self.partner_with_agent.id})
        )
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition.line"].with_user(
                self.salesperson
            ).create(
                {
                    "condition_id": condition.id,
                    "product_tmpl_id": self.product_template_b.id,
                    "seller_discount": 5.0,
                }
            )

    def test_condition_create_within_agent_limits_ok(self):
        """Create succeeds when values are within agent profile limits."""
        condition = (
            self.env["partner.commercial.condition"]
            .with_user(self.salesperson)
            .create(
                {
                    "partner_id": self.partner_with_agent.id,
                    "cash_discount": 1.5,
                    "fob_discount": 0.5,
                    "seller_discount": 2.0,
                }
            )
        )
        self.assertTrue(condition.id)
        self.assertAlmostEqual(condition.cash_discount, 1.5, places=2)

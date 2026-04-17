# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestComputeSalesProfileId(CommercialPolicyTestCommon):
    """Coverage for _compute_sales_profile_id — team profile fallback."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_team_profile_fallback_when_salesperson_has_no_profile(self):
        """Team profile is used when salesperson has no individual profile."""
        # Remove the individual profile from the salesperson
        self.salesperson.partner_id.sales_profile_id = False

        # Assign the agent_profile to the team
        self.team.sales_profile_id = self.agent_profile

        # Create the order with this salesperson and team
        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "user_id": self.salesperson.id,
                "team_id": self.team.id,
            }
        )

        self.assertEqual(
            order.sales_profile_id,
            self.agent_profile,
            "Order should inherit the team's sales profile when salesperson has none.",
        )

    def test_individual_profile_takes_precedence_over_team_profile(self):
        """Salesperson profile takes precedence over the team profile."""
        self.salesperson.partner_id.sales_profile_id = self.agent_profile
        self.team.sales_profile_id = self.internal_profile

        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "user_id": self.salesperson.id,
                "team_id": self.team.id,
            }
        )

        self.assertEqual(
            order.sales_profile_id,
            self.agent_profile,
            "Individual salesperson profile should take precedence over team profile.",
        )

    def test_no_profile_when_neither_salesperson_nor_team_has_one(self):
        """Profile is False when neither salesperson nor team has one."""
        self.salesperson.partner_id.sales_profile_id = False
        self.team.sales_profile_id = False
        # Clear company default so profile can't resolve via fallback
        self.env.company.default_sales_profile_id = False

        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "user_id": self.salesperson.id,
                "team_id": self.team.id,
            }
        )

        self.assertFalse(
            order.sales_profile_id,
            "Profile should be False when neither salesperson nor team has one.",
        )


@tagged("post_install", "-at_install")
class TestComputePaymentTermAvgDays(CommercialPolicyTestCommon):
    """Coverage for _compute_payment_term_avg_days — all branches."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_no_payment_term_gives_zero_avg_days(self):
        """avg days is 0.0 when the order has no payment term."""
        order = self._create_order()
        order.payment_term_id = False
        self.assertAlmostEqual(
            order.payment_term_avg_days,
            0.0,
            places=2,
            msg="No payment term should yield 0 avg days.",
        )

    def test_balance_line_gives_correct_avg_days(self):
        """A single balance line yields its days value as the avg."""
        order = self._create_order()
        order.payment_term_id = self.payment_term_short  # 15 days balance
        self.assertAlmostEqual(
            order.payment_term_avg_days,
            15.0,
            places=2,
            msg="Balance line of 15 days should give avg_days=15.",
        )

    def test_percent_line_and_balance_gives_weighted_avg(self):
        """Percent line + balance remainder yields correct weighted average."""
        percent_then_balance = self.env["account.payment.term"].create(
            {
                "name": "50% at 30 + balance at 60",
                "line_ids": [
                    (0, 0, {"value": "percent", "value_amount": 50.0, "days": 30}),
                    (0, 0, {"value": "balance", "days": 60}),
                ],
            }
        )
        order = self._create_order()
        order.payment_term_id = percent_then_balance
        # Weighted avg: (30*50 + 60*50) / 100 = 45
        self.assertAlmostEqual(
            order.payment_term_avg_days,
            45.0,
            places=2,
            msg="Weighted average of 30d@50%% + 60d@50%% should be 45 days.",
        )

    def test_months_in_term_line_converted_to_days(self):
        """Term line days = months*30 + days are summed correctly."""
        one_month_term = self.env["account.payment.term"].create(
            {
                "name": "1 Month (30 days via months field)",
                "line_ids": [
                    (0, 0, {"value": "balance", "months": 1, "days": 0}),
                ],
            }
        )
        order = self._create_order()
        order.payment_term_id = one_month_term
        # months=1 → 1*30 + 0 = 30 days; weight = 100 (balance); avg = 30
        self.assertAlmostEqual(
            order.payment_term_avg_days,
            30.0,
            places=2,
            msg="1 month term should give avg_days=30.",
        )


@tagged("post_install", "-at_install")
class TestDirectPriceEdit(CommercialPolicyTestCommon):
    """Coverage for _check_direct_price_edit — admin bypass."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_admin_can_edit_price_directly(self):
        """Admin user (group_system) can write price_unit directly."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        admin = self.env.ref("base.user_admin")
        line.with_user(admin).write({"price_unit": 999.0})
        self.assertAlmostEqual(line.price_unit, 999.0, places=2)


@tagged("post_install", "-at_install")
class TestOnchangeCashFobDiscount(CommercialPolicyTestCommon):
    """Coverage for _onchange_cash_fob_discount — direct call with/without profile."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_onchange_updates_line_discount_when_profile_set(self):
        """Lines get combined cash+fob discount applied when profile is active."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        order.cash_discount = 2.0
        order.fob_discount = 1.0

        # Call onchange directly (not triggered by ORM writes in tests)
        order._onchange_cash_fob_discount()

        expected_discount = 3.0  # 2.0 + 1.0
        self.assertAlmostEqual(
            line.discount,
            expected_discount,
            places=2,
            msg="Line discount should be cash + fob discount.",
        )

    def test_onchange_updates_discount_value_when_profile_set(self):
        """discount_value is recomputed proportionally to qty * price_unit."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        # price_unit is set by the profile engine; use what it computed
        order.cash_discount = 2.0
        order.fob_discount = 1.0

        order._onchange_cash_fob_discount()

        expected_value = (line.product_uom_qty * line.price_unit) * 3.0 / 100
        self.assertAlmostEqual(
            line.discount_value,
            expected_value,
            places=2,
            msg="discount_value should equal qty * price_unit * discount/100.",
        )

    def test_onchange_does_nothing_when_no_profile_no_condition(self):
        """Lines are not touched when the order has no profile or condition."""
        self.salesperson.partner_id.sales_profile_id = False
        # Clear company default so profile can't resolve via fallback
        self.env.company.default_sales_profile_id = False
        self.customer.commercial_condition_id = False
        order = self._create_order()
        self.assertFalse(order.sales_profile_id)
        self.assertFalse(order.commercial_condition_id)
        line = self._create_order_line(order)
        original_discount = line.discount

        order.cash_discount = 5.0
        order.fob_discount = 2.0
        order._onchange_cash_fob_discount()

        self.assertAlmostEqual(
            line.discount,
            original_discount,
            places=2,
            msg="Line discount should remain unchanged when no policy is active.",
        )

    def test_onchange_skips_section_lines(self):
        """Section lines (no product) are skipped in cash/fob onchange."""
        order = self._create_order()
        self._create_order_line(order)
        section = self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "display_type": "line_section",
                "name": "Section",
            }
        )
        order.cash_discount = 3.0
        order._onchange_cash_fob_discount()
        # Section line should not have discount applied
        self.assertFalse(section.product_id)


@tagged("post_install", "-at_install")
class TestOnchangeProductWarningNoProfile(CommercialPolicyTestCommon):
    """Coverage for _onchange_product_id_apply_condition warning path."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_onchange_returns_warning_when_no_profile(self):
        """Onchange returns warning dict when order has no sales profile."""
        self.salesperson.partner_id.sales_profile_id = False
        self.env.company.default_sales_profile_id = False
        self.customer.agent_ids = [(5,)]
        order = self._create_order()
        # Force recompute after clearing all profile sources
        order._compute_sales_profile_id()
        self.assertFalse(order.sales_profile_id)
        line = self._create_order_line(order, seller_discount=0.0)
        result = line._onchange_product_id_apply_condition()
        self.assertIn("warning", result)
        self.assertIn("No Sales Profile", result["warning"]["title"])

    def test_onchange_returns_early_without_product(self):
        """Onchange returns None when line has no product."""
        order = self._create_order()
        line = self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "display_type": "line_section",
                "name": "Section",
            }
        )
        result = line._onchange_product_id_apply_condition()
        self.assertIsNone(result)

    def test_onchange_skips_condition_when_none(self):
        """Onchange does not apply condition when order has none."""
        self.customer.commercial_condition_id = False
        self.env["partner.commercial.condition"].browse(self.condition.id).unlink()
        order = self._create_order()
        self.assertFalse(order.commercial_condition_id)
        line = self._create_order_line(order, seller_discount=0.0)
        result = line._onchange_product_id_apply_condition()
        # No condition → no profile → onchange returns a warning about
        # missing profile instead of silently applying no condition.
        self.assertIn("warning", result)
        self.assertAlmostEqual(line.seller_discount, 0.0, places=2)


@tagged("post_install", "-at_install")
class TestOnchangeCommercialConditionId(CommercialPolicyTestCommon):
    """Coverage for _onchange_commercial_condition_id."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_onchange_sets_pricelist_when_condition_has_one(self):
        """Pricelist is set from the condition when the condition has a pricelist."""
        order = self._create_order()
        # condition set via partner compute

        order._onchange_commercial_condition_id()

        self.assertEqual(
            order.pricelist_id,
            self.condition.pricelist_id,
            "Pricelist should be set from the commercial condition.",
        )

    def test_onchange_applies_condition_to_existing_lines(self):
        """_apply_condition_to_line is called for each order line on onchange."""
        order = self._create_order()
        line = self._create_order_line(order)
        # condition set via partner compute

        order._onchange_commercial_condition_id()

        # Condition has seller_discount = 5.0 (general)
        self.assertAlmostEqual(
            line.seller_discount,
            self.condition.seller_discount,
            places=2,
            msg="Line seller_discount should match condition general.",
        )


@tagged("post_install", "-at_install")
class TestApplyConditionToLine(CommercialPolicyTestCommon):
    """Coverage for _apply_condition_to_line — all resolution branches."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_variant_line_overrides_general_discount(self):
        """Variant-specific condition line takes precedence over general discount."""
        # Create a variant-level condition line for product_a
        self.env["partner.commercial.condition.line"].with_user(
            self.director_user
        ).create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product",
                "product_id": self.product_a.id,
                "seller_discount": 7.0,
                "extra_discount": 1.5,
            }
        )
        order = self._create_order()
        line = self._create_order_line(order)

        order._apply_condition_to_line(line, self.condition)

        self.assertAlmostEqual(line.seller_discount, 7.0, places=2)
        self.assertAlmostEqual(line.extra_discount, 1.5, places=2)

    def test_template_line_overrides_general_discount(self):
        """Template-specific condition line is used when no variant line exists."""
        # Create a template-level condition line for product_template_b
        self.env["partner.commercial.condition.line"].with_user(
            self.director_user
        ).create(
            {
                "condition_id": self.condition.id,
                "product_tmpl_id": self.product_template_b.id,
                "seller_discount": 6.0,
                "extra_discount": 0.5,
            }
        )
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_b)

        order._apply_condition_to_line(line, self.condition)

        self.assertAlmostEqual(line.seller_discount, 6.0, places=2)
        self.assertAlmostEqual(line.extra_discount, 0.5, places=2)

    def test_general_discount_applied_when_no_specific_line(self):
        """General condition seller_discount is applied when no specific line exists."""
        order = self._create_order()
        line = self._create_order_line(order, product=self.product_b)

        order._apply_condition_to_line(line, self.condition)

        # condition.seller_discount = 5.0 (general)
        self.assertAlmostEqual(
            line.seller_discount, self.condition.seller_discount, places=2
        )
        self.assertAlmostEqual(line.extra_discount, 0.0, places=2)

    def test_no_condition_returns_early_without_changes(self):
        """No changes are made to the line when condition is False."""
        order = self._create_order()
        line = self._create_order_line(order)
        line.seller_discount = 3.0

        order._apply_condition_to_line(line, False)

        self.assertAlmostEqual(
            line.seller_discount,
            3.0,
            places=2,
            msg="seller_discount should not change when condition is False.",
        )

    def test_no_product_on_line_returns_early_without_changes(self):
        """No changes are made when the line has no product set."""
        order = self._create_order()
        # Create an empty line section to have a line without product_id
        line = self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "display_type": "line_section",
                "name": "Section",
            }
        )
        # Ensure product_id is not set (section lines have no product)
        self.assertFalse(line.product_id)

        # Should return early without error
        order._apply_condition_to_line(line, self.condition)

    def test_discount_fixed_is_set_to_true_after_apply(self):
        """discount_fixed is set to True after condition is applied to a line."""
        order = self._create_order()
        line = self._create_order_line(order)

        order._apply_condition_to_line(line, self.condition)

        self.assertTrue(
            line.discount_fixed,
            "discount_fixed should be True after applying condition to line.",
        )

    def test_onchange_product_applies_condition(self):
        """Onchange applies seller_discount from condition to line."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=0.0)
        line._onchange_product_id_apply_condition()
        self.assertAlmostEqual(line.seller_discount, 5.0, places=2)

    def test_onchange_recalculates_price_unit(self):
        """Seller discount onchange recalculates price_unit."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        self.assertTrue(line.reference_price, "Line should have a reference price")
        line.seller_discount = 5.0
        line._onchange_seller_extra_discount()
        expected = line.reference_price * (1 - 5.0 / 100)
        self.assertAlmostEqual(line.price_unit, expected, places=2)

    def test_internal_band_below_minimum_sets_director_level(self):
        """Order amount below band minimum sets approval level to director."""
        self._setup_internal_policy()
        # Switch to internal profile
        self.condition.applicable_profile_id = self.internal_profile
        order = self._create_order()
        self.assertEqual(order.sales_profile_id, self.internal_profile)
        self._create_order_line(order, qty=1, seller_discount=4.0, base_price=500.0)
        # Order amount ~500 is below band minimum 1000 for 4% discount
        self.assertEqual(order.discount_approval_level, "director")


@tagged("post_install", "-at_install")
class TestActionOpenSaveConditionWizard(CommercialPolicyTestCommon):
    """Coverage for action_open_save_condition_wizard — variant-level source."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_wizard_line_save_as_variant_when_source_is_variant(self):
        """Wizard line defaults to 'variant' save_as for variant source."""
        # Create a variant-level condition line for product_a so the source is 'variant'
        self.env["partner.commercial.condition.line"].with_user(
            self.director_user
        ).create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product",
                "product_id": self.product_a.id,
                "seller_discount": 4.0,
                "extra_discount": 0.0,
            }
        )
        order = self._create_order()
        # Add product_a line with a different discount to trigger a wizard line
        line = self._create_order_line(order)
        line.seller_discount = 8.0  # different from condition variant line's 4.0

        result = order.action_open_save_condition_wizard()

        wizard = self.env["tr.save.condition.wizard"].browse(result["res_id"])
        # The wizard line for product_a should default to 'variant'
        product_a_wizard_line = wizard.line_ids.filtered(
            lambda wl: wl.product_id == self.product_a
        )
        self.assertTrue(product_a_wizard_line, "Expected wizard line for product_a.")
        self.assertEqual(
            product_a_wizard_line.save_as,
            "variant",
            "save_as should be 'variant' when the source discount is variant-level.",
        )

    def test_wizard_returns_correct_action_type(self):
        """action_open_save_condition_wizard returns an ir.actions.act_window."""
        order = self._create_order()
        result = order.action_open_save_condition_wizard()
        self.assertEqual(result["type"], "ir.actions.act_window")
        self.assertEqual(result["res_model"], "tr.save.condition.wizard")


@tagged("post_install", "-at_install")
class TestGetGeneralSellerDiscount(CommercialPolicyTestCommon):
    """Coverage for _get_general_seller_discount — mixed discounts, no condition."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_mixed_discounts_returns_condition_general_discount(self):
        """Mixed line discounts → falls back to condition.seller_discount."""
        order = self._create_order()
        self._create_order_line(order, seller_discount=3.0)
        self._create_order_line(order, product=self.product_b, seller_discount=7.0)

        result = order._get_general_seller_discount()

        self.assertAlmostEqual(
            result,
            self.condition.seller_discount,
            places=2,
            msg="Mixed discounts should return condition's general seller_discount.",
        )

    def test_all_same_discount_returns_that_discount(self):
        """When all lines share the same discount, it is returned directly."""
        order = self._create_order()
        self._create_order_line(order, seller_discount=5.0)
        self._create_order_line(order, product=self.product_b, seller_discount=5.0)

        result = order._get_general_seller_discount()

        self.assertAlmostEqual(result, 5.0, places=2)

    def test_no_lines_returns_zero(self):
        """Returns 0.0 when the order has no product lines."""
        order = self._create_order()

        result = order._get_general_seller_discount()

        self.assertAlmostEqual(result, 0.0, places=2)

    def test_no_general_rule_returns_empty(self):
        """When profile has no matching rule, empty recordset is returned."""
        self.general_rule.unlink()
        order = self._create_order()
        line = self._create_order_line(
            order, product=self.product_b, seller_discount=0.0
        )
        rule = line._get_applicable_rule()
        self.assertFalse(rule)

    def test_commission_rate_zero_when_no_rule(self):
        """Commission rate is 0 when no applicable rule exists."""
        self.general_rule.unlink()
        order = self._create_order()
        line = self._create_order_line(
            order, product=self.product_b, seller_discount=0.0
        )
        self.assertAlmostEqual(line.commission_rate, 0.0, places=2)


@tagged("post_install", "-at_install")
class TestComputePricelistId(CommercialPolicyTestCommon):
    """Coverage for _compute_pricelist_id — condition with/without pricelist."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_pricelist_from_condition_with_pricelist(self):
        """Pricelist is taken from the condition when it has one."""
        order = self._create_order()
        # condition has pricelist_id set in _setup_commercial_policy
        # condition set via partner compute
        # Trigger recompute
        order._compute_pricelist_id()

        self.assertEqual(
            order.pricelist_id,
            self.condition.pricelist_id,
            "pricelist_id should come from the commercial condition.",
        )

    def test_pricelist_from_condition_applied_to_order(self):
        """Order pricelist comes from the condition's pricelist."""
        other_pricelist = self.env["product.pricelist"].create(
            {"name": "Other PL", "currency_id": self.env.ref("base.BRL").id}
        )
        condition_other = (
            self.env["partner.commercial.condition"]
            .with_user(self.director_user)
            .create(
                {
                    "partner_id": self.customer_group.id,
                    "pricelist_id": other_pricelist.id,
                    "cash_discount": 0.5,
                    "fob_discount": 0.0,
                    "seller_discount": 1.0,
                }
            )
        )
        self.customer_group.commercial_condition_id = condition_other
        order = self._create_order(partner_id=self.customer_group.id)
        order._compute_pricelist_id()
        self.assertEqual(
            order.pricelist_id,
            other_pricelist,
            "Order pricelist should come from the commercial condition.",
        )


@tagged("post_install", "-at_install")
class TestCheckPricelistMatchesProfile(CommercialPolicyTestCommon):
    """Coverage for _check_pricelist_matches_profile on action_confirm."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_pricelist_not_in_allowed_blocks_confirm(self):
        """Confirmation is blocked when order pricelist is not in profile."""
        other_pricelist = self.env["product.pricelist"].create(
            {"name": "Other PL", "currency_id": self.env.ref("base.BRL").id}
        )
        order = self._create_order()
        self._create_order_line(order)
        order.pricelist_id = other_pricelist
        with self.assertRaises(ValidationError, msg="pricelist not allowed"):
            order.action_confirm()

    def test_pricelist_in_allowed_passes_confirm(self):
        """Confirmation passes when order pricelist is in profile."""
        order = self._create_order()
        self._create_order_line(order)
        order.pricelist_id = self.pricelist
        # Should not raise
        order._check_pricelist_matches_profile()

    def test_multiple_pricelists_allowed(self):
        """Confirmation passes when pricelist is one of several allowed."""
        other_pricelist = self.env["product.pricelist"].create(
            {"name": "Other PL", "currency_id": self.env.ref("base.BRL").id}
        )
        self.agent_profile.pricelist_ids = [(4, other_pricelist.id)]
        order = self._create_order()
        self._create_order_line(order)
        order.pricelist_id = other_pricelist
        # Should not raise — other_pricelist is now allowed
        order._check_pricelist_matches_profile()

    def test_empty_profile_pricelists_skips_check(self):
        """No error when profile has no pricelists set."""
        self.agent_profile.pricelist_ids = [(5,)]
        order = self._create_order()
        self._create_order_line(order)
        # Should not raise — empty means any pricelist accepted
        order._check_pricelist_matches_profile()


@tagged("post_install", "-at_install")
class TestPricelistWarning(CommercialPolicyTestCommon):
    """Coverage for _compute_pricelist_warning on sale.order."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_warning_when_pricelist_not_allowed(self):
        """Warning is set when order pricelist is not in profile's allowed list."""
        other_pricelist = self.env["product.pricelist"].create(
            {"name": "Other PL", "currency_id": self.env.ref("base.BRL").id}
        )
        order = self._create_order()
        order.pricelist_id = other_pricelist
        self.assertTrue(order.pricelist_warning)

    def test_no_warning_when_pricelist_allowed(self):
        """No warning when order pricelist is in profile's allowed list."""
        order = self._create_order()
        order.pricelist_id = self.pricelist
        self.assertFalse(order.pricelist_warning)

    def test_no_warning_when_profile_has_no_pricelists(self):
        """No warning when profile has no pricelists configured."""
        self.agent_profile.pricelist_ids = [(5,)]
        order = self._create_order()
        self.assertFalse(order.pricelist_warning)

    def test_no_warning_when_no_profile(self):
        """No warning when order has no sales profile."""
        order = self._create_order()
        order.sales_profile_id = False
        self.assertFalse(order.pricelist_warning)


@tagged("post_install", "-at_install")
class TestActionReloadConditions(CommercialPolicyTestCommon):
    """Coverage for action_reload_conditions on sale.order."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_reload_wizard_shows_pricelist_change(self):
        """Wizard shows pricelist change when condition differs from order."""
        other_pricelist = self.env["product.pricelist"].create(
            {"name": "New PL", "currency_id": self.env.ref("base.BRL").id}
        )
        self.agent_profile.pricelist_ids = [(4, other_pricelist.id)]
        order = self._create_order()
        self._create_order_line(order)
        self.condition.with_user(self.salesperson).pricelist_id = other_pricelist
        action = order.action_reload_conditions()
        wizard = self.env["tr.reload.condition.wizard"].browse(action["res_id"])
        self.assertTrue(wizard.pricelist_change)
        self.assertTrue(wizard.has_changes)

    def test_reload_wizard_shows_contractual_return_change(self):
        """Wizard shows contractual return change when condition differs."""
        order = self._create_order()
        self._create_order_line(order)
        # Change condition after order (snapshot stays at original value)
        self.condition.with_user(self.director_user).contractual_return = 8.0
        action = order.action_reload_conditions()
        wizard = self.env["tr.reload.condition.wizard"].browse(action["res_id"])
        self.assertTrue(wizard.contractual_return_change)
        self.assertTrue(wizard.has_changes)

    def test_reload_apply_updates_pricelist(self):
        """Apply reload updates pricelist from commercial condition."""
        other_pricelist = self.env["product.pricelist"].create(
            {"name": "New PL", "currency_id": self.env.ref("base.BRL").id}
        )
        self.agent_profile.pricelist_ids = [(4, other_pricelist.id)]
        order = self._create_order()
        self._create_order_line(order)
        self.condition.with_user(self.salesperson).pricelist_id = other_pricelist
        order._apply_reload_conditions()
        self.assertEqual(order.pricelist_id, other_pricelist)

    def test_reload_apply_reapplies_discounts(self):
        """Apply reload resets seller_discount from condition."""
        order = self._create_order()
        line = self._create_order_line(order)
        line.seller_discount = 3.0
        self.assertNotEqual(line.seller_discount, 5.0)
        order._apply_reload_conditions()
        self.assertAlmostEqual(line.seller_discount, 5.0)

    def test_reload_apply_updates_cash_fob_discounts(self):
        """Apply reload updates cash and fob discounts from condition."""
        order = self._create_order()
        self._create_order_line(order)
        order.with_context(tr_skip_price_protection=True).write(
            {"cash_discount": 0.0, "fob_discount": 0.0}
        )
        order._apply_reload_conditions()
        self.assertAlmostEqual(order.cash_discount, 2.0)
        self.assertAlmostEqual(order.fob_discount, 1.0)

    def test_reload_wizard_shows_line_changes(self):
        """Wizard shows line-level discount changes."""
        order = self._create_order()
        line = self._create_order_line(order)
        line.seller_discount = 3.0
        action = order.action_reload_conditions()
        wizard = self.env["tr.reload.condition.wizard"].browse(action["res_id"])
        self.assertTrue(wizard.line_ids)
        self.assertEqual(wizard.line_ids[0].product_id, self.product_a)

    def test_reload_wizard_no_changes(self):
        """Wizard shows no changes when order matches condition."""
        order = self._create_order()
        line = self._create_order_line(order)
        # Set line discounts to match condition (onchange doesn't run in tests)
        line.seller_discount = self.condition.seller_discount
        line.extra_discount = 0.0
        action = order.action_reload_conditions()
        wizard = self.env["tr.reload.condition.wizard"].browse(action["res_id"])
        self.assertFalse(wizard.has_changes)

    def test_reload_wizard_shows_cash_fob_changes(self):
        """Wizard shows cash and fob discount changes."""
        order = self._create_order()
        self._create_order_line(order)
        # Manually zero out cash/fob
        order.with_context(tr_skip_price_protection=True).write(
            {"cash_discount": 0.0, "fob_discount": 0.0}
        )
        action = order.action_reload_conditions()
        wizard = self.env["tr.reload.condition.wizard"].browse(action["res_id"])
        self.assertTrue(wizard.cash_discount_change)
        self.assertTrue(wizard.fob_discount_change)
        self.assertIn("→", wizard.cash_discount_change)
        self.assertIn("→", wizard.fob_discount_change)

    def test_reload_wizard_action_apply(self):
        """Wizard action_apply delegates to order and closes."""
        order = self._create_order()
        line = self._create_order_line(order)
        line.seller_discount = 2.0
        action = order.action_reload_conditions()
        wizard = self.env["tr.reload.condition.wizard"].browse(action["res_id"])
        result = wizard.action_apply()
        self.assertEqual(result["type"], "ir.actions.act_window_close")
        self.assertAlmostEqual(line.seller_discount, 5.0)

    def test_reload_wizard_line_description(self):
        """Wizard line has product display_name and change description."""
        order = self._create_order()
        line = self._create_order_line(order)
        line.seller_discount = 2.0
        action = order.action_reload_conditions()
        wizard = self.env["tr.reload.condition.wizard"].browse(action["res_id"])
        wizard_line = wizard.line_ids[0]
        self.assertEqual(wizard_line.line_description, self.product_a.display_name)
        self.assertIn("→", wizard_line.change_description)

    def test_reload_wizard_lines_changed_count(self):
        """Wizard computes correct lines_changed_count."""
        order = self._create_order()
        line_a = self._create_order_line(order, product=self.product_a)
        line_b = self._create_order_line(order, product=self.product_b)
        line_a.seller_discount = 2.0
        line_b.seller_discount = 2.0
        action = order.action_reload_conditions()
        wizard = self.env["tr.reload.condition.wizard"].browse(action["res_id"])
        self.assertEqual(wizard.lines_changed_count, 2)

    def test_reload_without_condition_raises(self):
        """Reload raises error when customer has no condition."""
        self.customer.commercial_condition_id = False
        order = self._create_order()
        order.invalidate_recordset(["commercial_condition_id"])
        with self.assertRaises(UserError):
            order.action_reload_conditions()

    def test_reload_wizard_matches_applied_values(self):
        """Wizard preview values match actual values after apply.

        Ensures the wizard uses the same calculation logic as the order
        (both go through policy_utils functions).
        """
        order = self._create_order()
        line = self._create_order_line(order)
        # Change discount so there's a difference
        line.seller_discount = 2.0
        line._recompute_price_unit_from_policy()
        # Open wizard to capture preview
        action = order.action_reload_conditions()
        wizard = self.env["tr.reload.condition.wizard"].browse(action["res_id"])
        self.assertTrue(wizard.line_ids, "Wizard should detect line changes")
        # Apply reload
        order._apply_reload_conditions()
        # After apply, line should match condition
        self.assertAlmostEqual(line.seller_discount, 5.0)
        # Verify the price chain is consistent: reference_price and price_unit
        # should match what policy_utils would calculate
        from ..models.policy_utils import (
            calc_price_unit,
            calc_reference_price,
            get_policy_rates,
        )

        tax_rate, freight_rate, admin_rate = get_policy_rates(self.env)
        expected_ref = calc_reference_price(
            line.base_price,
            order.contractual_return,
            tax_rate,
            freight_rate,
            admin_rate,
        )
        expected_unit = calc_price_unit(
            expected_ref, line.seller_discount, line.extra_discount
        )
        self.assertAlmostEqual(
            line.reference_price,
            expected_ref,
            places=2,
            msg="reference_price must match policy_utils calculation",
        )
        self.assertAlmostEqual(
            line.price_unit,
            expected_unit,
            places=2,
            msg="price_unit must match policy_utils calculation",
        )

    def test_reload_skips_invoiced_line_updates_non_invoiced(self):
        """Reload preserves price on invoiced lines, updates non-invoiced.

        End-to-end test: confirm → invoice partially → reload.
        """
        self.product_template_a.invoice_policy = "order"
        self.product_template_b.invoice_policy = "order"
        order = self._create_order()
        order.fiscal_operation_id = False
        line_a = self._create_order_line(order, product=self.product_a)
        line_b = self._create_order_line(order, product=self.product_b)

        order.action_confirm()

        # Invoice only line_a (remove line_b from invoice)
        invoice = order._create_invoices()
        inv_line_b = invoice.invoice_line_ids.filtered(
            lambda inv_line: inv_line.product_id == self.product_b
        )
        inv_line_b.unlink()
        invoice.action_post()

        self.assertTrue(line_a.qty_invoiced > 0, "line_a should be invoiced")
        self.assertAlmostEqual(line_b.qty_invoiced, 0.0, places=2)

        price_a_before = line_a.price_unit
        price_b_before = line_b.price_unit

        # Change condition
        self.condition.with_user(self.director_user).seller_discount = 8.0

        # Reload
        order._apply_reload_conditions()

        # Invoiced line keeps original price
        self.assertAlmostEqual(
            line_a.price_unit,
            price_a_before,
            places=2,
            msg="Invoiced line should not be repriced by reload.",
        )
        # Non-invoiced line gets updated
        self.assertNotAlmostEqual(
            line_b.price_unit,
            price_b_before,
            places=2,
            msg="Non-invoiced line should be repriced by reload.",
        )


@tagged("post_install", "-at_install")
class TestReloadLineChangesEdgeCases(CommercialPolicyTestCommon):
    """Coverage for _get_reload_line_changes edge cases."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_wizard_detects_extra_discount_change(self):
        """Wizard detects extra_discount difference."""
        order = self._create_order()
        line = self._create_order_line(order)
        line.seller_discount = self.condition.seller_discount
        line.extra_discount = 3.0  # condition has 0
        action = order.action_reload_conditions()
        wizard = self.env["tr.reload.condition.wizard"].browse(action["res_id"])
        self.assertTrue(wizard.line_ids)
        self.assertIn("Extra disc.", wizard.line_ids[0].change_description)

    def test_wizard_detects_unit_price_change(self):
        """Wizard detects unit price change from discount difference."""
        order = self._create_order()
        line = self._create_order_line(order)
        # Set seller_discount different → price_unit will differ
        line.seller_discount = 0.0
        line._recompute_price_unit_from_policy()
        action = order.action_reload_conditions()
        wizard = self.env["tr.reload.condition.wizard"].browse(action["res_id"])
        self.assertTrue(wizard.line_ids)
        self.assertIn("Unit price", wizard.line_ids[0].change_description)

    def test_get_fresh_base_price_no_pricelist(self):
        """_get_fresh_base_price returns 0 when no pricelist."""
        order = self._create_order()
        line = self._create_order_line(order)
        # Clear pricelist from condition and order
        self.condition.with_user(self.director_user).pricelist_id = False
        order.pricelist_id = False
        result = order._get_fresh_base_price(line, self.condition)
        self.assertEqual(result, 0.0)

    def test_apply_reload_without_condition_raises(self):
        """_apply_reload_conditions raises when no condition."""
        self.customer.commercial_condition_id = False
        order = self._create_order()
        order.invalidate_recordset(["commercial_condition_id"])
        with self.assertRaises(UserError):
            order._apply_reload_conditions()

    def test_apply_reload_without_pricelist_in_condition(self):
        """_apply_reload_conditions works when condition has no pricelist."""
        order = self._create_order()
        self._create_order_line(order)
        original_pl = order.pricelist_id
        self.condition.with_user(self.director_user).pricelist_id = False
        order._apply_reload_conditions()
        # Pricelist should remain unchanged
        self.assertEqual(order.pricelist_id, original_pl)


@tagged("post_install", "-at_install")
class TestPolicyUtilsEdgeCases(CommercialPolicyTestCommon):
    """Coverage for policy_utils edge cases."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    def test_calc_reference_price_zero_base(self):
        """calc_reference_price returns 0 when base_price is 0."""
        from ..models.policy_utils import calc_reference_price

        result = calc_reference_price(0.0, 10.0, 0.10, 0.05, 0.02)
        self.assertEqual(result, 0.0)

    def test_calc_price_unit_zero_reference(self):
        """calc_price_unit returns 0 when reference_price is 0."""
        from ..models.policy_utils import calc_price_unit

        result = calc_price_unit(0.0, 5.0, 2.0)
        self.assertEqual(result, 0.0)

    def test_calc_reference_price_no_return(self):
        """calc_reference_price equals base when no contractual return."""
        from ..models.policy_utils import calc_reference_price

        result = calc_reference_price(100.0, 0.0, 0.10, 0.05, 0.02)
        self.assertAlmostEqual(result, 100.0)

    def test_calc_price_unit_no_discount(self):
        """calc_price_unit equals reference when no discounts."""
        from ..models.policy_utils import calc_price_unit

        result = calc_price_unit(100.0, 0.0, 0.0)
        self.assertAlmostEqual(result, 100.0)


@tagged("post_install", "-at_install")
class TestActionViewCommercialCondition(CommercialPolicyTestCommon):
    """Coverage for action_view_commercial_condition."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_view_condition_returns_action(self):
        """Button returns a form action for the commercial condition."""
        order = self._create_order()
        # condition set via partner compute
        result = order.action_view_commercial_condition()
        self.assertEqual(result["res_model"], "partner.commercial.condition")
        self.assertEqual(result["res_id"], self.condition.id)

    def test_view_condition_without_condition_raises(self):
        """UserError is raised when no commercial condition exists."""
        self.customer.commercial_condition_id = False
        order = self._create_order()
        with self.assertRaises(UserError):
            order.action_view_commercial_condition()


@tagged("post_install", "-at_install")
class TestComputePunctualityDiscount(CommercialPolicyTestCommon):
    """Coverage for _compute_punctuality_discount."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_draft_order_gets_contractual_return(self):
        """Punctuality discount equals contractual_return for draft orders."""
        order = self._create_order()
        self._create_order_line(order)
        self.assertEqual(order.state, "draft")
        self.assertAlmostEqual(
            order.punctuality_discount,
            order.contractual_return,
            places=2,
        )

    def test_confirmed_order_keeps_punctuality_discount(self):
        """Punctuality discount is not recomputed for confirmed orders."""
        order = self._create_order()
        self._create_order_line(order)
        order.pricelist_id = self.pricelist
        order.action_confirm()
        self.assertEqual(order.state, "sale")
        current_value = order.punctuality_discount
        # Change contractual_return on condition — should NOT affect confirmed order
        self.condition.contractual_return = current_value + 5.0
        order.invalidate_recordset(["punctuality_discount"])
        self.assertAlmostEqual(
            order.punctuality_discount,
            current_value,
            places=2,
            msg="Confirmed order punctuality_discount should not change.",
        )


@tagged("post_install", "-at_install")
class TestComputeCommercialConditionWarning(CommercialPolicyTestCommon):
    """Coverage for _compute_commercial_condition_warning."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_warning_when_partner_has_no_condition(self):
        """Warning is set when partner exists but has no commercial condition."""
        partner_no_cond = self.env["res.partner"].create(
            {"name": "No Condition Partner"}
        )
        order = self._create_order(partner_id=partner_no_cond.id)
        self.assertFalse(order.commercial_condition_id)
        self.assertTrue(order.commercial_condition_warning)
        self.assertIn(partner_no_cond.display_name, order.commercial_condition_warning)

    def test_no_warning_when_partner_has_condition(self):
        """Warning is False when partner has a commercial condition."""
        order = self._create_order()
        self.assertTrue(order.commercial_condition_id)
        self.assertFalse(order.commercial_condition_warning)

    def test_no_warning_when_partner_cleared(self):
        """Warning is False when order partner is cleared."""
        order = self._create_order()
        # Simulate clearing partner via onchange (partner_id is required in DB)
        order.partner_id = False
        order._compute_commercial_condition_warning()
        self.assertFalse(order.commercial_condition_warning)

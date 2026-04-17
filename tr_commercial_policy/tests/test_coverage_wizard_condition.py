# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestGetCurrentUserProfileTeamFallback(CommercialPolicyTestCommon):
    """_get_applicable_profile team fallback path."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_profile_resolved_from_team_when_no_partner_profile(self):
        """User with no partner profile gets profile from their default team."""
        # User has no partner profile
        user_no_profile = self.env["res.users"].create(
            {
                "name": "Team Fallback User",
                "login": "team_fallback_user_tcp",
                "groups_id": [
                    (4, self.env.ref("sales_team.group_sale_salesman").id),
                ],
            }
        )
        self.assertFalse(user_no_profile.partner_id.sales_profile_id)

        # Assign profile to the team and make user a member
        self.team.sales_profile_id = self.agent_profile
        self.team.member_ids = [(4, user_no_profile.id)]

        CondModel = self.env["partner.commercial.condition"].with_user(user_no_profile)
        profile = CondModel._get_applicable_profile()
        self.assertEqual(
            profile,
            self.agent_profile,
            "Profile should resolve from team when user has no partner profile.",
        )


@tagged("post_install", "-at_install")
class TestGetPartnerProfileResolution(CommercialPolicyTestCommon):
    """_get_applicable_profile resolution paths."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_profile_resolved_from_agent(self):
        """Profile resolves from the condition partner's agent."""
        commission = self.env["commission"].create(
            {"name": "Agent Commission", "commission_type": "fixed", "fix_qty": 10.0}
        )
        agent = self.env["res.partner"].create(
            {
                "name": "Customer Agent",
                "agent": True,
                "commission_id": commission.id,
                "sales_profile_id": self.agent_profile.id,
            }
        )
        self.customer.agent_ids = [(4, agent.id)]
        profile = self.condition._get_applicable_profile()
        self.assertEqual(profile, self.agent_profile)

    def test_profile_falls_back_to_company_default(self):
        """Profile falls back to company default when partner has no agent."""
        self.customer.agent_ids = [(5,)]
        profile = self.condition._get_applicable_profile()
        self.assertEqual(profile, self.env.company.default_sales_profile_id)

    def test_condition_line_delegates_to_condition(self):
        """Condition line resolves profile via its parent condition."""
        line = self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "product_tmpl_id": self.product_template_b.id,
                "seller_discount": 3.0,
            }
        )
        profile = line._get_applicable_profile()
        self.assertEqual(profile, self.agent_profile)

    def test_condition_line_empty_recordset_uses_company_default(self):
        """Empty condition line recordset falls back to company default."""
        CondLine = self.env["partner.commercial.condition.line"]
        profile = CondLine._get_applicable_profile()
        self.assertEqual(profile, self.env.company.default_sales_profile_id)


@tagged("post_install", "-at_install")
class TestApplicableProfileReactivity(CommercialPolicyTestCommon):
    """Tests that applicable_profile_id recomputes when sources change."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_change_agent_profile_recomputes(self):
        """Changing the agent's profile recomputes applicable_profile_id."""
        commission = self.env["commission"].create(
            {"name": "Reactivity Comm", "commission_type": "fixed", "fix_qty": 10.0}
        )
        agent = self.env["res.partner"].create(
            {
                "name": "Reactivity Agent",
                "agent": True,
                "commission_id": commission.id,
                "sales_profile_id": self.agent_profile.id,
            }
        )
        self.customer.agent_ids = [(4, agent.id)]
        self.assertEqual(self.condition.applicable_profile_id, self.agent_profile)
        # Change agent's profile
        other_profile = self.env["tr.sales.profile"].create(
            {
                "name": "Other Profile",
                "profile_type": "agent",
                "cash_discount_max": 3.0,
                "fob_discount_max": 1.0,
                "cash_term_avg_days_max": 15,
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
        agent.sales_profile_id = other_profile
        self.assertEqual(self.condition.applicable_profile_id, other_profile)

    def test_remove_agent_falls_to_company_default(self):
        """Removing agent from partner falls back to company default."""
        self.customer.agent_ids = [(5,)]
        self.assertEqual(
            self.condition.applicable_profile_id,
            self.env.company.default_sales_profile_id,
        )

    def test_change_company_default_recomputes(self):
        """Changing company default recomputes for conditions without agent."""
        self.customer.agent_ids = [(5,)]
        other_profile = self.env["tr.sales.profile"].create(
            {
                "name": "New Default",
                "profile_type": "agent",
                "cash_discount_max": 5.0,
                "fob_discount_max": 2.0,
                "cash_term_avg_days_max": 30,
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
        self.env.company.default_sales_profile_id = other_profile
        self.assertEqual(self.condition.applicable_profile_id, other_profile)

    def test_order_reflects_condition_profile_change(self):
        """Order sales_profile_id updates when condition profile changes."""
        commission = self.env["commission"].create(
            {"name": "Order React Comm", "commission_type": "fixed", "fix_qty": 10.0}
        )
        agent = self.env["res.partner"].create(
            {
                "name": "Order React Agent",
                "agent": True,
                "commission_id": commission.id,
                "sales_profile_id": self.agent_profile.id,
            }
        )
        self.customer.agent_ids = [(4, agent.id)]
        order = self._create_order()
        self.assertEqual(order.sales_profile_id, self.agent_profile)
        # Change agent profile
        other_profile = self.env["tr.sales.profile"].create(
            {
                "name": "Changed Profile",
                "profile_type": "agent",
                "cash_discount_max": 2.0,
                "fob_discount_max": 1.0,
                "cash_term_avg_days_max": 10,
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
        agent.sales_profile_id = other_profile
        self.assertEqual(
            order.sales_profile_id,
            other_profile,
            "Order should reflect condition's updated profile.",
        )

    def test_swap_partner_recomputes(self):
        """Changing condition's partner recomputes applicable_profile."""
        new_partner = self.env["res.partner"].create({"name": "New Partner"})
        # New partner has no agent → falls to company default
        self.condition.with_user(self.director_user).partner_id = new_partner
        self.assertEqual(
            self.condition.applicable_profile_id,
            self.env.company.default_sales_profile_id,
        )


@tagged("post_install", "-at_install")
class TestValidateSellerDiscountOnCreate(CommercialPolicyTestCommon):
    """_validate_seller_discount called during create with partner arg."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_internal_policy()
        # Profile now resolves from condition's partner (agent → company default).
        # Set company default to internal profile so validation uses order value bands.
        cls.env.company.default_sales_profile_id = cls.internal_profile
        cls.salesperson.groups_id = [
            (4, cls.env.ref("sales_team.group_sale_salesman").id),
            (
                4,
                cls.env.ref("tr_commercial_policy.group_sales_manager").id,
            ),
        ]

    def test_create_with_seller_discount_exceeds_band_blocked(self):
        """create() with seller_discount above band max raises ValidationError.

        The partner arg path is exercised because self is empty during create.
        """
        new_customer = self.env["res.partner"].create(
            {"name": "New Customer for Create Test"}
        )
        # No confirmed orders for new_customer → avg = 0 → band max = 0%
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition"].with_user(self.salesperson).create(
                {
                    "partner_id": new_customer.id,
                    "seller_discount": 5.0,
                }
            )

    def test_create_with_seller_discount_zero_ok(self):
        """create() with seller_discount=0 is accepted even with no orders."""
        new_customer = self.env["res.partner"].create(
            {"name": "New Customer Zero Discount"}
        )
        condition = (
            self.env["partner.commercial.condition"]
            .with_user(self.salesperson)
            .create(
                {
                    "partner_id": new_customer.id,
                    "seller_discount": 0.0,
                }
            )
        )
        self.assertAlmostEqual(condition.seller_discount, 0.0, places=2)


@tagged("post_install", "-at_install")
class TestValidateSellerDiscountAgentProfile(CommercialPolicyTestCommon):
    """_validate_seller_discount for agent profile via condition write."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        # salesperson already has agent_profile assigned in _setup_commercial_policy
        # agent_profile general_rule has commission bands up to 10% → max=10.0

    def test_agent_seller_discount_exceeds_max_blocked(self):
        """Agent profile: seller_discount above commission band max is blocked."""
        # agent_profile max seller_discount = 10.0 (from commission bands)
        # Use manager_user (has write access to conditions)
        self.manager_user.partner_id.sales_profile_id = self.agent_profile
        with self.assertRaises(ValidationError):
            self.condition.with_user(self.manager_user).write({"seller_discount": 11.0})

    def test_agent_seller_discount_within_max_ok(self):
        """Agent profile: seller_discount within commission band max is accepted."""
        self.manager_user.partner_id.sales_profile_id = self.agent_profile
        self.condition.with_user(self.manager_user).write({"seller_discount": 9.0})
        self.assertAlmostEqual(self.condition.seller_discount, 9.0, places=2)


@tagged("post_install", "-at_install")
class TestDirectorBypassLineDiscountLimits(CommercialPolicyTestCommon):
    """Director bypasses _validate_line_discount_limits."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_director_can_write_any_seller_discount_on_line(self):
        """Director can set an extremely high seller_discount on condition line."""
        line = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.director_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "product_tmpl_id": self.product_template_b.id,
                    "seller_discount": 99.0,
                }
            )
        )
        self.assertAlmostEqual(line.seller_discount, 99.0, places=2)

    def test_director_can_write_existing_line(self):
        """Director can write any discount to an existing condition line."""
        line = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.director_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "product_tmpl_id": self.product_template_b.id,
                    "seller_discount": 5.0,
                }
            )
        )
        line.with_user(self.director_user).write({"seller_discount": 99.0})
        self.assertAlmostEqual(line.seller_discount, 99.0, places=2)


@tagged("post_install", "-at_install")
class TestLineDiscountEarlyReturnNonDiscountFields(CommercialPolicyTestCommon):
    """Early return when writing non-discount fields on condition line."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_write_non_discount_field_no_profile_check(self):
        """Writing a non-discount field on condition line skips profile validation."""
        line = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.director_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "product_tmpl_id": self.product_template_b.id,
                    "seller_discount": 5.0,
                }
            )
        )
        # User with no profile — but writing only product_tmpl_id (no discount field)
        user_no_profile = self.env["res.users"].create(
            {
                "name": "No Profile Early Return",
                "login": "no_profile_early_return_tcp",
                "groups_id": [
                    (4, self.env.ref("sales_team.group_sale_salesman").id),
                    (
                        4,
                        self.env.ref("tr_commercial_policy.group_sales_manager").id,
                    ),
                ],
            }
        )
        # This must not raise AccessError because no discount fields are touched
        line.with_user(user_no_profile).write(
            {"product_tmpl_id": self.product_template_a.id}
        )
        self.assertEqual(line.product_tmpl_id, self.product_template_a)


@tagged("post_install", "-at_install")
class TestResolveRuleForProductTmplOnly(CommercialPolicyTestCommon):
    """_resolve_rule_for_product with product_tmpl_id only (no product_id)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        # Create a template-level rule on agent profile
        cls.tmpl_rule = cls.env["tr.sales.profile.rule"].create(
            {
                "profile_id": cls.agent_profile.id,
                "applied_on": "product_template",
                "product_tmpl_id": cls.product_template_a.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 3.0, "commission_rate": 5.0}),
                ],
            }
        )

    def test_resolve_rule_with_tmpl_only_returns_template_rule(self):
        """product_id=False + product_tmpl_id given → template rule found."""
        CondLine = self.env["partner.commercial.condition.line"]
        rule = CondLine._resolve_rule_for_product(
            self.agent_profile,
            product_id=False,
            product_tmpl_id=self.product_template_a.id,
        )
        self.assertEqual(rule, self.tmpl_rule)

    def test_seller_discount_on_line_with_tmpl_only_blocked_by_tmpl_rule(self):
        """seller_discount on template-only line blocked by template rule max (3%)."""
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition.line"].with_user(
                self.salesperson
            ).create(
                {
                    "condition_id": self.condition.id,
                    "product_tmpl_id": self.product_template_a.id,
                    "seller_discount": 4.0,  # exceeds tmpl_rule max of 3.0
                }
            )


@tagged("post_install", "-at_install")
class TestResolveRuleForProductCategoryWalkup(CommercialPolicyTestCommon):
    """_resolve_rule_for_product with category walk-up."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        # Create a category-level rule for the parent category (Chemicals)
        cls.categ_rule = cls.env["tr.sales.profile.rule"].create(
            {
                "profile_id": cls.agent_profile.id,
                "applied_on": "category",
                "categ_id": cls.categ_chemicals.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 4.0, "commission_rate": 8.0}),
                ],
            }
        )

    def test_resolve_rule_walks_up_to_parent_category(self):
        """Rule resolution walks up the category tree to find parent categ rule."""
        # product_b is in categ_solvents (child of categ_chemicals)
        # No rule for categ_solvents, but categ_chemicals has one
        CondLine = self.env["partner.commercial.condition.line"]
        rule = CondLine._resolve_rule_for_product(
            self.agent_profile,
            product_id=self.product_b.id,
            product_tmpl_id=False,
        )
        self.assertEqual(
            rule,
            self.categ_rule,
            "Should resolve to parent category rule via walk-up.",
        )

    def test_seller_discount_blocked_by_parent_category_rule(self):
        """Seller discount blocked when it exceeds the parent category rule max."""
        # categ_rule max = 4.0
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition.line"].with_user(
                self.salesperson
            ).create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product",
                    "product_id": self.product_b.id,
                    "seller_discount": 5.0,  # exceeds categ_rule max of 4.0
                }
            )


@tagged("post_install", "-at_install")
class TestCheckUniqueProductTemplateLine(CommercialPolicyTestCommon):
    """_check_unique_product for duplicate template-level lines (no product_id)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_duplicate_template_line_raises_validation_error(self):
        """Creating a second template-level line for same template raises error."""
        self.env["partner.commercial.condition.line"].with_user(
            self.director_user
        ).create(
            {
                "condition_id": self.condition.id,
                "product_tmpl_id": self.product_template_b.id,
                "seller_discount": 3.0,
            }
        )
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition.line"].with_user(
                self.director_user
            ).create(
                {
                    "condition_id": self.condition.id,
                    "product_tmpl_id": self.product_template_b.id,
                    "seller_discount": 5.0,
                }
            )


@tagged("post_install", "-at_install")
class TestCheckContractualReturn(CommercialPolicyTestCommon):
    """_check_contractual_return constraint."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_contractual_return_above_100_raises(self):
        """contractual_return > 100 raises ValidationError."""
        with self.assertRaises(ValidationError):
            self.condition.with_user(self.director_user).write(
                {"contractual_return": 101.0}
            )

    def test_contractual_return_negative_raises(self):
        """contractual_return < 0 raises ValidationError."""
        with self.assertRaises(ValidationError):
            self.condition.with_user(self.director_user).write(
                {"contractual_return": -1.0}
            )

    def test_contractual_return_100_ok(self):
        """contractual_return == 100 is valid."""
        self.condition.with_user(self.director_user).write(
            {"contractual_return": 100.0}
        )
        self.assertAlmostEqual(self.condition.contractual_return, 100.0, places=2)

    def test_contractual_return_0_ok(self):
        """contractual_return == 0 is valid."""
        self.condition.with_user(self.director_user).write({"contractual_return": 0.0})
        self.assertAlmostEqual(self.condition.contractual_return, 0.0, places=2)


@tagged("post_install", "-at_install")
class TestSaveConditionWizardProfileResolution(CommercialPolicyTestCommon):
    """save_condition_wizard._check_user_profile: non-director with profile."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_non_director_with_profile_can_save(self):
        """Non-director user can save when condition has a resolvable profile."""
        order = self._create_order()
        order.cash_discount = 3.0
        result = order.action_open_save_condition_wizard()
        wizard = self.env["tr.save.condition.wizard"].browse(result["res_id"])
        wizard.update_cash_discount = True
        wizard.with_user(self.salesperson).action_save()
        self.assertAlmostEqual(self.condition.cash_discount, 3.0, places=2)

    def test_line_wizard_non_director_with_profile_can_save(self):
        """Non-director user can save per-line wizard with resolvable profile."""
        order = self._create_order()
        line = self._create_order_line(
            order, product=self.product_b, seller_discount=8.0
        )
        result = line.action_open_save_condition_line_wizard()
        wizard = self.env["tr.save.condition.line.wizard"].browse(result["res_id"])
        wizard.save_as = "template"
        wizard.with_user(self.salesperson).action_save()
        cond_line = self.condition.line_ids.filtered(
            lambda cline: cline.product_tmpl_id == self.product_template_b
        )
        self.assertTrue(cond_line)


@tagged("post_install", "-at_install")
class TestSaveConditionWizardCheckUserProfileDirector(CommercialPolicyTestCommon):
    """save_condition_wizard._check_user_profile: director bypass."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_director_bypasses_profile_check(self):
        """Director can call action_save without a sales profile."""
        order = self._create_order()
        order.cash_discount = 3.0
        result = order.action_open_save_condition_wizard()
        wizard = self.env["tr.save.condition.wizard"].browse(result["res_id"])
        wizard.update_cash_discount = True
        # Director group bypasses _check_user_profile
        wizard.with_user(self.director_user).action_save()
        self.assertAlmostEqual(self.condition.cash_discount, 3.0, places=2)


@tagged("post_install", "-at_install")
class TestSaveConditionWizardCheckUserProfileNoProfile(CommercialPolicyTestCommon):
    """save_condition_wizard._check_user_profile: no-profile user raises AccessError."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_no_profile_no_director_raises_access_error(self):
        """User without sales profile and without director group raises AccessError."""
        # Clear company default so profile can't resolve via fallback
        self.env.company.default_sales_profile_id = False
        # Also remove agent from customer so profile can't resolve from agent
        self.customer.agent_ids = [(5,)]
        user_no_profile = self.env["res.users"].create(
            {
                "name": "No Profile No Director",
                "login": "no_profile_no_director_tcp",
                "groups_id": [
                    (4, self.env.ref("sales_team.group_sale_salesman").id),
                ],
            }
        )
        self.assertFalse(user_no_profile.partner_id.sales_profile_id)

        order = self._create_order()
        order.cash_discount = 3.0
        result = order.action_open_save_condition_wizard()
        wizard = self.env["tr.save.condition.wizard"].browse(result["res_id"])
        wizard.update_cash_discount = True
        with self.assertRaises(AccessError):
            wizard.with_user(user_no_profile).action_save()


@tagged("post_install", "-at_install")
class TestSaveConditionWizardComputeAllSameDiscountNoLines(CommercialPolicyTestCommon):
    """_compute_all_same_discount: all lines ignored → all_same_discount is True."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_all_same_discount_true_when_no_active_lines(self):
        """Wizard with all lines ignored has all_same_discount = True."""
        order = self._create_order()
        self._create_order_line(order, product=self.product_b, seller_discount=8.0)
        result = order.action_open_save_condition_wizard()
        wizard = self.env["tr.save.condition.wizard"].browse(result["res_id"])
        # Mark all lines as ignored
        wizard.line_ids.write({"ignore": True})
        # Recompute (stored field is compute, not stored — read it directly)
        self.assertTrue(
            wizard.all_same_discount,
            "all_same_discount should be True when all lines are ignored.",
        )

    def test_all_same_discount_true_when_no_wizard_lines(self):
        """Wizard with no product lines at all has all_same_discount = True."""
        order = self._create_order()
        # No order lines with different discounts → wizard has no product lines
        result = order.action_open_save_condition_wizard()
        wizard = self.env["tr.save.condition.wizard"].browse(result["res_id"])
        self.assertFalse(wizard.line_ids)
        self.assertTrue(wizard.all_same_discount)


@tagged("post_install", "-at_install")
class TestSaveConditionWizardSaveToConditionVariantUpdate(CommercialPolicyTestCommon):
    """_save_to_condition: variant save_as with existing line update path."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_save_as_variant_updates_existing_variant_line(self):
        """Saving as variant updates an existing variant line instead of creating."""
        # Pre-create a variant-level condition line
        existing = self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product",
                "product_id": self.product_b.id,
                "seller_discount": 3.0,
            }
        )

        order = self._create_order()
        self._create_order_line(order, product=self.product_b, seller_discount=8.0)
        result = order.action_open_save_condition_wizard()
        wizard = self.env["tr.save.condition.wizard"].browse(result["res_id"])
        wizard.line_ids.save_as = "variant"
        wizard.action_save()

        # Should update existing, not create a duplicate
        variant_lines = self.condition.line_ids.filtered(
            lambda line: line.product_id == self.product_b
        )
        self.assertEqual(len(variant_lines), 1)
        self.assertAlmostEqual(variant_lines.seller_discount, 8.0, places=2)
        self.assertEqual(variant_lines.id, existing.id)


@tagged("post_install", "-at_install")
class TestSaveConditionWizardActionSaveEmptyVals(CommercialPolicyTestCommon):
    """action_save with no general discount update checked (empty vals dict)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_action_save_no_update_flags_set(self):
        """action_save with no update flags does not modify general discounts."""
        original_cash = self.condition.cash_discount
        order = self._create_order()
        result = order.action_open_save_condition_wizard()
        wizard = self.env["tr.save.condition.wizard"].browse(result["res_id"])
        # All update flags remain False (default)
        wizard.action_save()
        self.assertAlmostEqual(
            self.condition.cash_discount,
            original_cash,
            places=2,
            msg="cash_discount should be unchanged when update flag is not set.",
        )


@tagged("post_install", "-at_install")
class TestSaveConditionLineWizardCheckUserProfileDirector(CommercialPolicyTestCommon):
    """save_condition_line_wizard._check_user_profile: director bypass."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_director_bypasses_profile_check(self):
        """Director can call action_save without a sales profile."""
        order = self._create_order()
        line = self._create_order_line(
            order, product=self.product_b, seller_discount=8.0
        )
        result = line.action_open_save_condition_line_wizard()
        wizard = self.env["tr.save.condition.line.wizard"].browse(result["res_id"])
        wizard.save_as = "template"
        # Director group bypasses _check_user_profile
        wizard.with_user(self.director_user).action_save()
        cond_line = self.condition.line_ids.filtered(
            lambda cline: cline.product_tmpl_id == self.product_template_b
            and cline.applied_on == "product_template"
        )
        self.assertTrue(cond_line)


@tagged("post_install", "-at_install")
class TestSaveConditionLineWizardCheckUserProfileNoProfile(CommercialPolicyTestCommon):
    """save_condition_line_wizard._check_user_profile: no-profile raises AccessError."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_no_profile_user_raises_access_error(self):
        """User without profile and without director group raises AccessError."""
        # Clear company default so profile can't resolve via fallback
        self.env.company.default_sales_profile_id = False
        # Also remove agent from customer so profile can't resolve from agent
        self.customer.agent_ids = [(5,)]
        user_no_profile = self.env["res.users"].create(
            {
                "name": "No Profile Line Wizard",
                "login": "no_profile_line_wizard_tcp",
                "groups_id": [
                    (4, self.env.ref("sales_team.group_sale_salesman").id),
                ],
            }
        )
        self.assertFalse(user_no_profile.partner_id.sales_profile_id)

        order = self._create_order()
        line = self._create_order_line(
            order, product=self.product_b, seller_discount=0.0
        )
        result = line.action_open_save_condition_line_wizard()
        wizard = self.env["tr.save.condition.line.wizard"].browse(result["res_id"])
        wizard.save_as = "template"
        with self.assertRaises(AccessError):
            wizard.with_user(user_no_profile).action_save()


@tagged("post_install", "-at_install")
class TestSaveConditionLineWizardSaveToConditionUpdate(CommercialPolicyTestCommon):
    """save_condition_line_wizard._save_to_condition: update existing line path."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_save_as_template_updates_existing_template_line(self):
        """Per-line wizard updates an existing template-level condition line."""
        existing = self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "product_tmpl_id": self.product_template_b.id,
                "seller_discount": 3.0,
            }
        )

        order = self._create_order()
        line = self._create_order_line(
            order, product=self.product_b, seller_discount=8.0
        )
        result = line.action_open_save_condition_line_wizard()
        wizard = self.env["tr.save.condition.line.wizard"].browse(result["res_id"])
        wizard.save_as = "template"
        wizard.action_save()

        template_lines = self.condition.line_ids.filtered(
            lambda cline: cline.product_tmpl_id == self.product_template_b
            and cline.applied_on == "product_template"
        )
        self.assertEqual(len(template_lines), 1)
        self.assertAlmostEqual(template_lines.seller_discount, 8.0, places=2)
        self.assertEqual(template_lines.id, existing.id)

    def test_save_as_variant_updates_existing_variant_line(self):
        """Per-line wizard updates an existing variant-level condition line."""
        existing = self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product",
                "product_id": self.product_b.id,
                "seller_discount": 3.0,
            }
        )

        order = self._create_order()
        line = self._create_order_line(
            order, product=self.product_b, seller_discount=8.0
        )
        result = line.action_open_save_condition_line_wizard()
        wizard = self.env["tr.save.condition.line.wizard"].browse(result["res_id"])
        wizard.save_as = "variant"
        wizard.action_save()

        variant_lines = self.condition.line_ids.filtered(
            lambda cline: cline.product_id == self.product_b
        )
        self.assertEqual(len(variant_lines), 1)
        self.assertAlmostEqual(variant_lines.seller_discount, 8.0, places=2)
        self.assertEqual(variant_lines.id, existing.id)

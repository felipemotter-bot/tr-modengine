# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestInternalProfileResolution(CommercialPolicyTestCommon):
    """Test the internal profile resolution chain on commercial condition.

    Chain: agent → salesperson → salesperson's team → partner's team → company.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Internal profile for team-based resolution
        cls.team_profile = cls.env["tr.sales.profile"].create(
            {
                "name": "Team Profile",
                "profile_type": "internal",
                "pricelist_ids": [(6, 0, [cls.pricelist.id])],
                "cash_discount_max": 6.0,
                "fob_discount_max": 4.0,
                "cash_term_avg_days_max": 35,
                "manager_extra_limit": 4.0,
                "rule_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "general",
                            "order_value_band_ids": [
                                (
                                    0,
                                    0,
                                    {
                                        "order_min_amount": 0.0,
                                        "seller_discount_max": 0.0,
                                    },
                                ),
                            ],
                        },
                    ),
                ],
            }
        )
        # Salesperson profile (different from team and internal)
        cls.salesperson_profile = cls.env["tr.sales.profile"].create(
            {
                "name": "Salesperson Profile",
                "profile_type": "internal",
                "pricelist_ids": [(6, 0, [cls.pricelist.id])],
                "cash_discount_max": 7.0,
                "fob_discount_max": 5.0,
                "cash_term_avg_days_max": 40,
                "manager_extra_limit": 5.0,
                "rule_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "general",
                            "order_value_band_ids": [
                                (
                                    0,
                                    0,
                                    {
                                        "order_min_amount": 0.0,
                                        "seller_discount_max": 0.0,
                                    },
                                ),
                            ],
                        },
                    ),
                ],
            }
        )
        # Clean customer: no agents, no salesperson, no team
        cls.clean_customer = cls.env["res.partner"].create({"name": "Clean Customer"})
        # Team with profile
        cls.team_with_profile = cls.env["crm.team"].create(
            {
                "name": "Team With Profile",
                "sales_profile_id": cls.team_profile.id,
            }
        )
        # Company default
        cls.env.company.default_sales_profile_id = cls.internal_profile

    def _create_condition(self, partner):
        """Create a commercial condition for the given partner."""
        return self.env["partner.commercial.condition"].create(
            {
                "partner_id": partner.id,
                "pricelist_id": self.pricelist.id,
            }
        )

    def test_agent_profile_takes_priority(self):
        """Agent's profile is used when partner has an agent."""
        agent = self.env["res.partner"].create(
            {
                "name": "Agent",
                "agent": True,
                "commission_id": self.env["commission"]
                .create(
                    {
                        "name": "C",
                        "commission_type": "fixed",
                        "fix_qty": 5.0,
                    }
                )
                .id,
                "sales_profile_id": self.agent_profile.id,
            }
        )
        self.clean_customer.agent_ids = [(4, agent.id)]
        condition = self._create_condition(self.clean_customer)
        self.assertEqual(condition.applicable_profile_id, self.agent_profile)
        self.assertEqual(condition.profile_source, "agent")

    def test_salesperson_team_without_profile_falls_to_partner_team(self):
        """Salesperson's team without profile falls to partner's team."""
        self.salesperson.partner_id.sales_profile_id = False
        empty_team = self.env["crm.team"].create({"name": "Empty SP Team"})
        self.salesperson.sale_team_id = empty_team
        self.clean_customer.user_id = self.salesperson
        self.clean_customer.team_id = self.team_with_profile
        condition = self._create_condition(self.clean_customer)
        self.assertEqual(condition.applicable_profile_id, self.team_profile)
        self.assertEqual(condition.profile_source, "partner_team")

    def test_salesperson_profile_without_agent(self):
        """Salesperson's profile is used when no agent exists."""
        self.salesperson.partner_id.sales_profile_id = self.salesperson_profile
        self.clean_customer.user_id = self.salesperson
        condition = self._create_condition(self.clean_customer)
        self.assertEqual(condition.applicable_profile_id, self.salesperson_profile)
        self.assertEqual(condition.profile_source, "salesperson")

    def test_salesperson_team_profile_when_salesperson_has_no_profile(self):
        """Salesperson's team profile is used when salesperson has no profile."""
        self.salesperson.partner_id.sales_profile_id = False
        self.salesperson.sale_team_id = self.team_with_profile
        self.clean_customer.user_id = self.salesperson
        condition = self._create_condition(self.clean_customer)
        self.assertEqual(condition.applicable_profile_id, self.team_profile)
        self.assertEqual(condition.profile_source, "salesperson_team")

    def test_partner_team_profile_when_no_salesperson(self):
        """Partner's team profile is used when there's no salesperson."""
        self.clean_customer.user_id = False
        self.clean_customer.team_id = self.team_with_profile
        condition = self._create_condition(self.clean_customer)
        self.assertEqual(condition.applicable_profile_id, self.team_profile)
        self.assertEqual(condition.profile_source, "partner_team")

    def test_company_default_fallback(self):
        """Company default is used when no other profile source exists."""
        self.clean_customer.user_id = False
        self.clean_customer.team_id = False
        condition = self._create_condition(self.clean_customer)
        self.assertEqual(condition.applicable_profile_id, self.internal_profile)
        self.assertEqual(condition.profile_source, "company")

    def test_salesperson_priority_over_team(self):
        """Salesperson profile takes priority over partner's team."""
        self.salesperson.partner_id.sales_profile_id = self.salesperson_profile
        self.clean_customer.user_id = self.salesperson
        self.clean_customer.team_id = self.team_with_profile
        condition = self._create_condition(self.clean_customer)
        self.assertEqual(condition.applicable_profile_id, self.salesperson_profile)
        self.assertEqual(condition.profile_source, "salesperson")

    def test_salesperson_team_priority_over_partner_team(self):
        """Salesperson's team profile takes priority over partner's team."""
        self.salesperson.partner_id.sales_profile_id = False
        other_team = self.env["crm.team"].create(
            {
                "name": "Other Team",
                "sales_profile_id": self.salesperson_profile.id,
            }
        )
        self.salesperson.sale_team_id = self.team_with_profile
        self.clean_customer.user_id = self.salesperson
        self.clean_customer.team_id = other_team
        condition = self._create_condition(self.clean_customer)
        # Salesperson's team (team_with_profile) takes priority
        self.assertEqual(condition.applicable_profile_id, self.team_profile)
        self.assertEqual(condition.profile_source, "salesperson_team")


@tagged("post_install", "-at_install")
class TestInternalProfileReactivity(CommercialPolicyTestCommon):
    """Test that applicable_profile_id recomputes when dependencies change."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.team_profile = cls.env["tr.sales.profile"].create(
            {
                "name": "Team Profile React",
                "profile_type": "internal",
                "pricelist_ids": [(6, 0, [cls.pricelist.id])],
                "cash_discount_max": 6.0,
                "fob_discount_max": 4.0,
                "cash_term_avg_days_max": 35,
                "manager_extra_limit": 4.0,
                "rule_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "general",
                            "order_value_band_ids": [
                                (
                                    0,
                                    0,
                                    {
                                        "order_min_amount": 0.0,
                                        "seller_discount_max": 0.0,
                                    },
                                ),
                            ],
                        },
                    ),
                ],
            }
        )
        cls.salesperson_profile = cls.env["tr.sales.profile"].create(
            {
                "name": "SP Profile React",
                "profile_type": "internal",
                "pricelist_ids": [(6, 0, [cls.pricelist.id])],
                "cash_discount_max": 7.0,
                "fob_discount_max": 5.0,
                "cash_term_avg_days_max": 40,
                "manager_extra_limit": 5.0,
                "rule_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "general",
                            "order_value_band_ids": [
                                (
                                    0,
                                    0,
                                    {
                                        "order_min_amount": 0.0,
                                        "seller_discount_max": 0.0,
                                    },
                                ),
                            ],
                        },
                    ),
                ],
            }
        )
        cls.team_with_profile = cls.env["crm.team"].create(
            {
                "name": "Team React",
                "sales_profile_id": cls.team_profile.id,
            }
        )
        cls.clean_customer = cls.env["res.partner"].create({"name": "React Customer"})
        cls.env.company.default_sales_profile_id = cls.internal_profile

    def _create_condition(self, partner):
        return self.env["partner.commercial.condition"].create(
            {
                "partner_id": partner.id,
                "pricelist_id": self.pricelist.id,
            }
        )

    def test_react_assign_salesperson(self):
        """Assigning a salesperson with profile recomputes the condition."""
        condition = self._create_condition(self.clean_customer)
        self.assertEqual(condition.applicable_profile_id, self.internal_profile)
        # Assign salesperson with profile
        self.salesperson.partner_id.sales_profile_id = self.salesperson_profile
        self.clean_customer.user_id = self.salesperson
        self.assertEqual(condition.applicable_profile_id, self.salesperson_profile)

    def test_react_change_salesperson_profile(self):
        """Changing salesperson's profile recomputes the condition."""
        self.salesperson.partner_id.sales_profile_id = self.salesperson_profile
        self.clean_customer.user_id = self.salesperson
        condition = self._create_condition(self.clean_customer)
        self.assertEqual(condition.applicable_profile_id, self.salesperson_profile)
        # Change salesperson's profile
        self.salesperson.partner_id.sales_profile_id = self.team_profile
        self.assertEqual(condition.applicable_profile_id, self.team_profile)

    def test_react_remove_salesperson_falls_to_team(self):
        """Removing salesperson falls back to partner's team."""
        self.salesperson.partner_id.sales_profile_id = self.salesperson_profile
        self.clean_customer.user_id = self.salesperson
        self.clean_customer.team_id = self.team_with_profile
        condition = self._create_condition(self.clean_customer)
        self.assertEqual(condition.applicable_profile_id, self.salesperson_profile)
        # Remove salesperson → falls to partner's team
        self.clean_customer.user_id = False
        self.assertEqual(condition.applicable_profile_id, self.team_profile)

    def test_react_change_team_profile(self):
        """Changing team's profile recomputes the condition."""
        self.clean_customer.team_id = self.team_with_profile
        condition = self._create_condition(self.clean_customer)
        self.assertEqual(condition.applicable_profile_id, self.team_profile)
        # Change team's profile
        self.team_with_profile.sales_profile_id = self.salesperson_profile
        self.assertEqual(condition.applicable_profile_id, self.salesperson_profile)

    def test_react_salesperson_team_change(self):
        """Changing salesperson's team recomputes the condition."""
        self.salesperson.partner_id.sales_profile_id = False
        self.salesperson.sale_team_id = self.team_with_profile
        self.clean_customer.user_id = self.salesperson
        condition = self._create_condition(self.clean_customer)
        self.assertEqual(condition.applicable_profile_id, self.team_profile)
        # Move salesperson to a team without profile
        empty_team = self.env["crm.team"].create({"name": "Empty Team"})
        self.salesperson.sale_team_id = empty_team
        # Falls to company default (no partner team set)
        self.assertEqual(condition.applicable_profile_id, self.internal_profile)

    def test_react_change_company_default(self):
        """Changing company default recomputes conditions using it."""
        condition = self._create_condition(self.clean_customer)
        self.assertEqual(condition.applicable_profile_id, self.internal_profile)
        # Change company default
        self.env.company.default_sales_profile_id = self.team_profile
        self.assertEqual(condition.applicable_profile_id, self.team_profile)

    def test_react_assign_partner_team(self):
        """Assigning a team to the partner recomputes the condition."""
        condition = self._create_condition(self.clean_customer)
        self.assertEqual(condition.applicable_profile_id, self.internal_profile)
        # Assign team with profile
        self.clean_customer.team_id = self.team_with_profile
        self.assertEqual(condition.applicable_profile_id, self.team_profile)

    def test_react_remove_partner_team(self):
        """Removing the partner's team falls back to company default."""
        self.clean_customer.team_id = self.team_with_profile
        condition = self._create_condition(self.clean_customer)
        self.assertEqual(condition.applicable_profile_id, self.team_profile)
        # Remove team
        self.clean_customer.team_id = False
        self.assertEqual(condition.applicable_profile_id, self.internal_profile)


@tagged("post_install", "-at_install")
class TestInternalContextCompatibility(CommercialPolicyTestCommon):
    """Test _check_internal_context_compatibility with the new chain."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls.other_internal_profile = cls.env["tr.sales.profile"].create(
            {
                "name": "Other Internal",
                "profile_type": "internal",
                "pricelist_ids": [(6, 0, [cls.pricelist.id])],
                "cash_discount_max": 6.0,
                "fob_discount_max": 4.0,
                "cash_term_avg_days_max": 35,
                "manager_extra_limit": 4.0,
                "rule_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "general",
                            "order_value_band_ids": [
                                (
                                    0,
                                    0,
                                    {
                                        "order_min_amount": 0.0,
                                        "seller_discount_max": 0.0,
                                    },
                                ),
                            ],
                        },
                    ),
                ],
            }
        )

    def _make_internal_order(self):
        """Create an order that resolves to internal_profile."""
        self.customer.agent_ids = [(5,)]
        self.salesperson.partner_id.sales_profile_id = False
        self.env.company.default_sales_profile_id = self.internal_profile
        self.condition._compute_applicable_profile()
        order = self._create_order()
        self._create_order_line(order)
        return order

    def test_compatible_salesperson_team_passes(self):
        """Order passes when salesperson's team matches the condition profile."""
        team = self.env["crm.team"].create(
            {
                "name": "Compatible Team",
                "sales_profile_id": self.internal_profile.id,
            }
        )
        self.salesperson.sale_team_id = team
        order = self._make_internal_order()
        # salesperson has no direct profile, team has internal_profile → matches
        order.with_user(self.salesperson).action_confirm()
        self.assertEqual(order.state, "sale")

    def test_incompatible_salesperson_team_blocks(self):
        """Order blocks when salesperson's team has a different profile."""
        team = self.env["crm.team"].create(
            {
                "name": "Different Team",
                "sales_profile_id": self.other_internal_profile.id,
            }
        )
        self.salesperson.sale_team_id = team
        order = self._make_internal_order()
        with self.assertRaises(UserError):
            order.with_user(self.salesperson).action_confirm()


@tagged("post_install", "-at_install")
class TestProfileChangeNoBreakDraft(CommercialPolicyTestCommon):
    """Changing the profile must not break existing draft orders."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_profile_change_does_not_break_draft_order(self):
        """Switching to a stricter profile does not raise on draft orders."""
        # Create a valid order with cash_discount from condition (2.0)
        order = self._create_order()
        self._create_order_line(order)
        self.assertEqual(order.state, "draft")

        # Set cash_discount to 4.0 via SQL (within current profile max 5.0)
        order.env.cr.execute(
            "UPDATE sale_order SET cash_discount = %s WHERE id = %s",
            [4.0, order.id],
        )
        order.invalidate_recordset(fnames=["cash_discount"])

        # Now switch to a stricter profile (max 3.0) via team assignment
        strict_profile = self.env["tr.sales.profile"].create(
            {
                "name": "Strict Profile",
                "profile_type": "internal",
                "pricelist_ids": [(6, 0, [self.pricelist.id])],
                "cash_discount_max": 3.0,
                "fob_discount_max": 2.0,
                "cash_term_avg_days_max": 20,
                "manager_extra_limit": 2.0,
                "rule_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "general",
                            "order_value_band_ids": [
                                (
                                    0,
                                    0,
                                    {
                                        "order_min_amount": 0.0,
                                        "seller_discount_max": 0.0,
                                    },
                                ),
                            ],
                        },
                    ),
                ],
            }
        )
        # Remove agents so resolution falls to company default
        self.customer.agent_ids = [(5,)]
        self.salesperson.partner_id.sales_profile_id = False

        # This should NOT raise — draft orders must survive profile changes
        self.env.company.default_sales_profile_id = strict_profile

        # Order now has stricter profile, cash_discount 4.0 > max 3.0
        order.invalidate_recordset(fnames=["sales_profile_id"])
        self.assertEqual(order.sales_profile_id, strict_profile)

        # But action_confirm SHOULD block (non-director gets ValidationError)
        with self.assertRaises(ValidationError):
            order.with_user(self.salesperson).action_confirm()

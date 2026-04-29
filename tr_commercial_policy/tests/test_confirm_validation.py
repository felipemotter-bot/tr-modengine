# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestConfirmValidation(CommercialPolicyTestCommon):
    """Test discount validation at order confirmation time.

    Covers the scenario where discounts bypass ORM constrains
    (e.g., profile assigned after discounts were set) and validates
    that action_confirm catches the violations as a safety net.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def _create_order_with_profile(self, **kwargs):
        """Helper to create a basic order with a profile and one line."""
        vals = {
            "partner_id": self.customer.id,
            "user_id": self.salesperson.id,
            "order_line": [
                (
                    0,
                    0,
                    {
                        "product_id": self.product_a.id,
                        "product_uom_qty": 1,
                        "price_unit": 100.0,
                    },
                ),
            ],
        }
        vals.update(kwargs)
        order = self.env["sale.order"].create(vals)
        self.assertTrue(order.sales_profile_id)
        return order

    def test_confirm_skips_policy_without_company_default(self):
        """Order confirms freely when company has no default profile."""
        self.env.company.default_sales_profile_id = False
        self.customer.commercial_condition_id = False
        self.customer.agent_ids = [(5,)]
        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "product_uom_qty": 1,
                            "price_unit": 100.0,
                        },
                    ),
                ],
            }
        )
        self.assertFalse(order._is_commercial_policy_applicable())
        order.action_confirm()
        self.assertEqual(order.state, "sale")

    def test_confirm_without_profile_raises(self):
        """Cannot confirm an order without a sales profile."""
        self.salesperson.partner_id.sales_profile_id = False
        self.team.sales_profile_id = False
        # Clear customer condition so profile doesn't resolve from it
        self.customer.commercial_condition_id = False
        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "user_id": self.salesperson.id,
                "team_id": self.team.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "product_uom_qty": 1,
                            "price_unit": 100.0,
                        },
                    ),
                ],
            }
        )
        self.assertFalse(order.sales_profile_id)
        with self.assertRaises(UserError):
            order.action_confirm()

    def test_confirm_with_profile_ok(self):
        """Can confirm an order with a valid sales profile and no violations."""
        order = self._create_order_with_profile()
        order.action_confirm()
        self.assertEqual(order.state, "sale")

    def test_confirm_seller_discount_exceeds_profile(self):
        """Confirm blocks when seller discount exceeds profile limit.

        Computed field recomputation (seller_discount_max) doesn't trigger
        @api.constrains, so action_confirm must catch this violation.
        """
        order = self._create_order_with_profile()
        line = order.order_line[0]
        # Bypass constrains via SQL — simulate discount set before profile
        self.env.cr.execute(
            "UPDATE sale_order_line SET seller_discount = %s WHERE id = %s",
            [50.0, line.id],
        )
        line.invalidate_recordset(fnames=["seller_discount"])
        self.assertEqual(line.seller_discount, 50.0)
        with self.assertRaises(ValidationError):
            order.with_user(self.salesperson).action_confirm()

    def test_confirm_seller_discount_within_limit_ok(self):
        """Confirm succeeds when seller discount is within profile limit."""
        order = self._create_order_with_profile()
        # seller_discount_max from general rule is 10%
        order.order_line[0].seller_discount = 5.0
        order.action_confirm()
        self.assertEqual(order.state, "sale")

    def test_confirm_profile_set_late_validates_all(self):
        """Full scenario: profile assigned to order, discounts set within
        limits, confirm validates everything."""
        order = self._create_order()
        # Profile resolves from salesperson (agent_profile: cash_max=5,
        # fob_max=3, seller_max=10)
        self.assertTrue(order.sales_profile_id)
        order.cash_discount = 3.0
        order.fob_discount = 2.0
        for line in order.order_line:
            line.seller_discount = 8.0
        order.action_confirm()
        self.assertEqual(order.state, "sale")


@tagged("post_install", "-at_install")
class TestPartnerAgentValidation(CommercialPolicyTestCommon):
    """Agent constraints and profile check on confirmation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    def test_customer_cannot_have_two_agents(self):
        """Adding a second agent to customer raises ValidationError."""
        agent2 = self.env["res.partner"].create(
            {
                "name": "Second Agent",
                "agent": True,
                "commission_id": self.commission.id,
                "sales_profile_id": self.agent_profile.id,
            }
        )
        with self.assertRaises(ValidationError):
            self.customer.agent_ids = [(4, agent2.id)]

    def test_agent_without_profile_cannot_be_linked(self):
        """Agent without sales_profile_id cannot be linked to customer."""
        agent_no_profile = self.env["res.partner"].create(
            {
                "name": "No Profile Agent",
                "agent": True,
                "commission_id": self.commission.id,
            }
        )
        partner = self.env["res.partner"].create({"name": "New Customer"})
        with self.assertRaises(ValidationError):
            partner.agent_ids = [(4, agent_no_profile.id)]

    def test_confirm_with_agent_without_profile_gives_specific_error(self):
        """Confirm error mentions agent name when agent has no profile.

        Simulates a real scenario: agent loses profile after being linked
        to the customer (e.g. profile removed by admin, data migration).
        """
        # Remove profile from agent via ORM — this does not trigger
        # _check_agent_has_profile because that constraint is on the
        # customer's agent_ids, not on the agent's sales_profile_id.
        self.agent_partner.sales_profile_id = False
        # Clear customer condition so profile doesn't resolve from it
        # but keep agents so the agent-specific error triggers
        self.customer.commercial_condition_id = False
        order = self._create_order()
        self._create_order_line(order, seller_discount=0.0)
        with self.assertRaises(UserError) as ctx:
            order.action_confirm()
        self.assertIn("Test Agent", str(ctx.exception))


@tagged("post_install", "-at_install")
class TestCommissionConsistencyOnConfirm(CommercialPolicyTestCommon):
    """Commission lines must be consistent with customer agents."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()

    def test_commission_on_line_without_customer_agent_blocks_confirm(self):
        """Order with agent commissions but no customer agent blocks confirm."""
        # Create an agent with profile and commission
        temp_agent = self.env["res.partner"].create(
            {
                "name": "Temp Agent",
                "agent": True,
                "commission_id": self.env["commission"]
                .create(
                    {
                        "name": "Temp Commission",
                        "commission_type": "fixed",
                        "fix_qty": 5.0,
                    }
                )
                .id,
                "sales_profile_id": self.agent_profile.id,
            }
        )
        # Create partner with the agent attached
        partner = self.env["res.partner"].create(
            {
                "name": "Will Lose Agent Customer",
                "agent_ids": [(4, temp_agent.id)],
            }
        )
        # Create condition for the partner
        condition = (
            self.env["partner.commercial.condition"]
            .with_user(self.director_user)
            .create({"partner_id": partner.id})
        )
        partner.commercial_condition_id = condition
        # Create order — agent commissions auto-created on the line
        order = self.env["sale.order"].create(
            {
                "partner_id": partner.id,
                "user_id": self.salesperson.id,
            }
        )
        line = self._create_order_line(order, seller_discount=3.0)
        # Verify agent commission exists on the line
        self.assertTrue(line.agent_ids, "Agent commission should exist on the line")
        # Remove the agent from the partner via ORM
        # (agent_ids depends on order_id.partner_id, NOT partner.agent_ids,
        #  so line.agent_ids won't recompute when partner loses its agent)
        partner.write({"agent_ids": [(3, temp_agent.id)]})
        self.assertFalse(partner.agent_ids, "Partner should have no agents now")
        # Line still has the commission records from before
        self.assertTrue(line.agent_ids, "Commission should still be on the line")
        # Confirm should detect orphan commissions
        with self.assertRaises(UserError):
            order.action_confirm()


@tagged("post_install", "-at_install")
class TestProfileConsistency(CommercialPolicyTestCommon):
    """Coverage for _check_profile_consistency on action_confirm."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    def test_no_profile_skips_check(self):
        """Consistency check is skipped when order has no profile.

        Uses a partner without a commercial condition so the order
        naturally lacks a profile (post-constraint, condition existing
        without profile is impossible).
        """
        partner_no_cond = self.env["res.partner"].create(
            {"name": "Customer Without Condition Consistency"}
        )
        order = self.env["sale.order"].create(
            {
                "partner_id": partner_no_cond.id,
                "pricelist_id": self.pricelist.id,
            }
        )
        self.assertFalse(order.sales_profile_id)
        # Should not raise (no profile → nothing to check)
        order._check_profile_consistency()

    def test_agent_profile_lines_match(self):
        """Agent profile: confirm OK when line agents match order profile."""
        order = self._create_order()
        self._create_order_line(order, seller_discount=3.0)
        # agent_partner is on customer and on lines, same profile
        self.assertTrue(order.sales_profile_id)
        self.assertEqual(order.sales_profile_id.profile_type, "agent")
        order._check_profile_consistency()

    def test_agent_profile_line_mismatch_raises(self):
        """Agent profile: blocks when line agent has different profile."""
        other_profile = self.env["tr.sales.profile"].create(
            {
                "name": "Other Agent Profile",
                "profile_type": "agent",
                "cash_discount_max": 3.0,
                "fob_discount_max": 1.0,
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
        # Create a second agent with different profile
        other_agent = self.env["res.partner"].create(
            {
                "name": "Other Agent",
                "agent": True,
                "commission_id": self.commission.id,
                "sales_profile_id": other_profile.id,
            }
        )
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        # Replace agent on the line with a different one
        line.agent_ids.write({"agent_id": other_agent.id})
        with self.assertRaises(UserError):
            order._check_profile_consistency()

    def test_internal_profile_blocks_agent_commissions(self):
        """Internal profile: blocks when lines have agent commissions."""
        self._setup_internal_policy()
        self.customer.agent_ids = [(5,)]
        self.env.company.default_sales_profile_id = self.internal_profile
        order = self._create_order()
        self.assertEqual(order.sales_profile_id.profile_type, "internal")
        line = self._create_order_line(order, seller_discount=0.0)
        # Manually add agent commission to the line
        line.agent_ids = [
            (
                0,
                0,
                {
                    "agent_id": self.agent_partner.id,
                    "commission_id": self.commission.id,
                },
            )
        ]
        with self.assertRaises(UserError):
            order._check_profile_consistency()

    def test_internal_context_salesperson_mismatch_raises(self):
        """Internal profile: blocks when salesperson has different profile."""
        self._setup_internal_policy()
        other_internal = self.env["tr.sales.profile"].create(
            {
                "name": "Other Internal",
                "profile_type": "internal",
                "cash_discount_max": 1.0,
                "fob_discount_max": 1.0,
                "cash_term_avg_days_max": 10,
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
        self.customer.agent_ids = [(5,)]
        self.env.company.default_sales_profile_id = self.internal_profile
        # Salesperson has a different internal profile
        self.salesperson.partner_id.sales_profile_id = other_internal
        order = self._create_order()
        self.assertEqual(order.sales_profile_id, self.internal_profile)
        with self.assertRaises(UserError):
            order._check_profile_consistency()

    def test_internal_context_salesperson_matches_ok(self):
        """Internal profile: OK when salesperson has same profile."""
        self._setup_internal_policy()
        self.customer.agent_ids = [(5,)]
        self.env.company.default_sales_profile_id = self.internal_profile
        self.salesperson.partner_id.sales_profile_id = self.internal_profile
        order = self._create_order()
        order._check_profile_consistency()

    def test_internal_context_team_mismatch_raises(self):
        """Internal profile: blocks when team has different profile."""
        self._setup_internal_policy()
        other_internal = self.env["tr.sales.profile"].create(
            {
                "name": "Other Internal",
                "profile_type": "internal",
                "cash_discount_max": 1.0,
                "fob_discount_max": 1.0,
                "cash_term_avg_days_max": 10,
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
        self.customer.agent_ids = [(5,)]
        self.env.company.default_sales_profile_id = self.internal_profile
        self.salesperson.partner_id.sales_profile_id = False
        self.team.sales_profile_id = other_internal
        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "user_id": self.salesperson.id,
                "team_id": self.team.id,
            }
        )
        with self.assertRaises(UserError):
            order._check_profile_consistency()

    def test_internal_profile_matches_company_default(self):
        """Internal profile: OK when company default matches."""
        self._setup_internal_policy()
        self.customer.agent_ids = [(5,)]
        self.salesperson.partner_id.sales_profile_id = False
        self.team.sales_profile_id = False
        self.env.company.default_sales_profile_id = self.internal_profile
        order = self._create_order()
        order._compute_sales_profile_id()
        self.assertEqual(order.sales_profile_id, self.internal_profile)
        order._check_profile_consistency()

    def test_internal_profile_company_default_recomputes(self):
        """Changing company default recomputes applicable_profile on condition."""
        self._setup_internal_policy()
        self.customer.agent_ids = [(5,)]
        self.env.company.default_sales_profile_id = self.internal_profile
        # Condition picks up internal_profile via company default
        self.assertEqual(self.condition.applicable_profile_id, self.internal_profile)

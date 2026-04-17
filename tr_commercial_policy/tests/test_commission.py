# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestDynamicCommission(CommercialPolicyTestCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    def test_commission_rate_low_discount(self):
        """Commission rate uses the first band when seller_discount <= 5%."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        self.assertEqual(line.commission_rate, 10.0)

    def test_commission_rate_high_discount(self):
        """Commission rate uses the second band when seller_discount > 5% and <= 10%."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=7.0)
        self.assertEqual(line.commission_rate, 7.0)

    def test_commission_rate_exact_boundary(self):
        """Commission rate at exact boundary (5%) uses the first band."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=5.0)
        self.assertEqual(line.commission_rate, 10.0)

    def test_commission_rate_zero_discount(self):
        """Commission rate with zero discount uses the first band."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=0.0)
        self.assertEqual(line.commission_rate, 10.0)

    def test_commission_rate_no_bands(self):
        """Commission rate is 0 when there are no bands on the rule."""
        self.general_rule.commission_band_ids.unlink()
        # Without bands, seller_discount_max is 0, so use seller_discount=0
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=0.0)
        self.assertEqual(line.commission_rate, 0.0)

    def test_commission_rate_no_profile(self):
        """Commission rate is 0 when order has no sales profile."""
        # Remove agent from customer so profile doesn't resolve from agent
        self.customer.agent_ids = [(5,)]
        self.salesperson.partner_id.sales_profile_id = False
        # Clear company default so profile can't resolve via fallback
        self.env.company.default_sales_profile_id = False
        order = self._create_order()
        # Without profile, seller_discount_max=0, so can only create with 0
        line = self._create_order_line(order, seller_discount=0.0)
        self.assertEqual(line.commission_rate, 0.0)

    def test_agent_commission_resolved_by_managed(self):
        """Agent line gets a managed commission with the band rate."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        self.assertEqual(line.commission_rate, 10.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        self.assertTrue(agent_line)
        # Managed commission should have the band rate
        self.assertTrue(agent_line.commission_id.tr_managed)
        self.assertAlmostEqual(agent_line.commission_id.tr_rate, 10.0, places=2)
        # Amount should be > 0 (calculated by the formula)
        self.assertGreater(agent_line.amount, 0.0)

    def test_agent_higher_discount_lower_rate(self):
        """Higher discount resolves to lower commission rate in managed."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=7.0)
        self.assertEqual(line.commission_rate, 7.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        self.assertTrue(agent_line.commission_id.tr_managed)
        self.assertAlmostEqual(agent_line.commission_id.tr_rate, 7.0, places=2)

    def test_agent_fallback_no_bands(self):
        """Agent falls back to standard commission when no bands exist."""
        self.general_rule.commission_band_ids.unlink()
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=0.0)
        self.assertEqual(line.commission_rate, 0.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        # No bands → no managed → uses agent's default commission
        self.assertFalse(agent_line.commission_id.tr_managed)
        self.assertEqual(agent_line.commission_id, self.commission)

    def test_agent_internal_profile_uses_standard(self):
        """Internal profiles do not use managed — fallback to standard."""
        self.agent_partner.sales_profile_id = self.internal_profile
        self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": self.internal_profile.id,
                "applied_on": "general",
                "order_value_band_ids": [
                    (0, 0, {"order_min_amount": 0.0, "seller_discount_max": 10.0}),
                ],
            }
        )
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        # Internal profile → no managed → standard commission
        self.assertFalse(agent_line.commission_id.tr_managed)

    def test_get_applicable_rule_variant(self):
        """_get_applicable_rule returns variant-specific rule when available."""
        variant_rule = self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": self.agent_profile.id,
                "applied_on": "product",
                "product_id": self.product_a.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 15.0, "commission_rate": 8.0}),
                ],
            }
        )
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        rule = line._get_applicable_rule()
        self.assertEqual(rule, variant_rule)

    def test_get_applicable_rule_general_fallback(self):
        """_get_applicable_rule falls back to general rule."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        rule = line._get_applicable_rule()
        self.assertEqual(rule, self.general_rule)

    def test_agent_line_gets_managed_commission(self):
        """Agent line commission_id is resolved by the managed, not agent default."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        self.assertEqual(line.commission_rate, 10.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        self.assertTrue(agent_line)
        # The commission should be a tr_managed record with rate 10.0
        self.assertTrue(agent_line.commission_id.tr_managed)
        self.assertAlmostEqual(agent_line.commission_id.tr_rate, 10.0, places=2)

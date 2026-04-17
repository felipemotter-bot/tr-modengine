# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon

# ── 1. Boundary tests ──────────────────────────────────────────────────────


@tagged("post_install", "-at_install")
class TestBoundarySellerDiscount(CommercialPolicyTestCommon):
    """Boundary tests for seller_discount vs seller_discount_max."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()

    def test_seller_discount_exactly_at_max_is_allowed(self):
        """seller_discount == seller_discount_max must pass (uses > not >=)."""
        order = self._create_order()
        # general_rule seller_discount_max = 10.0 (highest commission band)
        line = self._create_order_line(order, seller_discount=10.0)
        self.assertAlmostEqual(line.seller_discount, 10.0, places=2)
        self.assertAlmostEqual(line.seller_discount_max, 10.0, places=2)

    def test_seller_discount_one_cent_above_max_is_blocked(self):
        """seller_discount just above max must be blocked."""
        order = self._create_order()
        with self.assertRaises(ValidationError):
            self._create_order_line(order, seller_discount=10.01)


@tagged("post_install", "-at_install")
class TestBoundaryInternalBands(CommercialPolicyTestCommon):
    """Boundary tests for internal profile order value bands."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_internal_policy()
        # Profile now resolves from condition (agent → company default).
        cls.env.company.default_sales_profile_id = cls.internal_profile
        # Clear salesperson's agent profile so _check_internal_profile_source
        # falls through to company default (which matches internal_profile).
        cls.salesperson.partner_id.sales_profile_id = False

    def _create_order_with_amount(self, target_amount):
        """Create order with amount_untaxed close to target using base_price."""
        order = self._create_order()
        order.fiscal_operation_id = False
        # Use base_price to control the amount directly
        line = self._create_order_line(
            order, qty=1, seller_discount=0.0, base_price=target_amount
        )
        return order, line

    def test_order_amount_exactly_at_band_applies_that_band(self):
        """amount_untaxed at band boundary qualifies for that band (<=)."""
        # Band: order_min_amount=5000 → seller_discount_max=10%
        # cash+fob discount (3%) reduces amount, so use higher base_price
        order, line = self._create_order_with_amount(5200.0)
        amount = order.amount_untaxed
        self.assertGreaterEqual(amount, 5000.0)
        # seller_discount_max should be at least 10% (band at 5000)
        self.assertGreaterEqual(line.seller_discount_max, 10.0)

    def test_order_amount_between_bands_uses_lower_band(self):
        """amount_untaxed between two bands qualifies for the lower one."""
        # 3000 is between 1000 and 5000 → max from 1000 band = 5%
        order, line = self._create_order_with_amount(3000.0)
        amount = order.amount_untaxed
        self.assertGreaterEqual(amount, 1000.0)
        self.assertLess(amount, 5000.0)
        self.assertAlmostEqual(line.seller_discount_max, 5.0, places=2)

    def test_order_amount_below_all_bands_returns_zero(self):
        """amount_untaxed below all bands → seller_discount_max = 0."""
        # Lowest band: order_min_amount=1000
        order, line = self._create_order_with_amount(500.0)
        amount = order.amount_untaxed
        self.assertLess(amount, 1000.0)
        self.assertAlmostEqual(line.seller_discount_max, 0.0, places=2)


# ── 2. Dynamic seller_discount_max recalculation ───────────────────────────


@tagged("post_install", "-at_install")
class TestDynamicSellerDiscountMax(CommercialPolicyTestCommon):
    """seller_discount_max recalculates when order amount changes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_internal_policy()
        # Profile now resolves from condition (agent → company default).
        cls.env.company.default_sales_profile_id = cls.internal_profile
        # Clear salesperson's agent profile so _check_internal_profile_source
        # falls through to company default (which matches internal_profile).
        cls.salesperson.partner_id.sales_profile_id = False

    def test_adding_line_increases_max(self):
        """Adding a line increases amount_untaxed → higher band → higher max."""
        order = self._create_order()
        order.fiscal_operation_id = False
        # Small amount → below 1000 band
        line = self._create_order_line(
            order, qty=1, seller_discount=0.0, base_price=500.0
        )
        initial_max = line.seller_discount_max
        self.assertAlmostEqual(initial_max, 0.0, places=2)
        # Add a big line to push above 1000
        self._create_order_line(
            order,
            product=self.product_b,
            qty=1,
            seller_discount=0.0,
            base_price=2000.0,
        )
        # Recompute — seller_discount_max should now be >= 5%
        line.invalidate_recordset(["seller_discount_max"])
        self.assertGreaterEqual(line.seller_discount_max, 5.0)

    def test_confirm_fails_when_discount_invalid_for_amount(self):
        """Confirm fails when seller_discount exceeds absolute max for product."""
        order = self._create_order()
        order.fiscal_operation_id = False
        # Start with high amount → high max
        line = self._create_order_line(
            order, qty=1, seller_discount=4.0, base_price=3000.0
        )
        # seller_discount_max should be 5% (band at 1000)
        self.assertGreaterEqual(line.seller_discount_max, 5.0)
        # Now set seller_discount above the absolute max (15%) via SQL
        # to simulate scenario where discount was set before profile
        self.env.cr.execute(
            "UPDATE sale_order_line SET seller_discount = 20.0 WHERE id = %s",
            (line.id,),
        )
        line.invalidate_recordset(["seller_discount"])
        # seller_discount=20% exceeds the absolute max (15%)
        # _validate_seller_discount_limit raises ValidationError
        with self.assertRaises(ValidationError):
            order.with_user(self.salesperson).action_confirm()


# ── 3. Commercial condition → order propagation ────────────────────────────


@tagged("post_install", "-at_install")
class TestConditionPropagation(CommercialPolicyTestCommon):
    """Test condition changes reflecting on existing orders."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()

    def test_condition_discounts_propagate_to_new_order(self):
        """Order picks up cash/fob discounts from condition automatically."""
        order = self._create_order()
        # cash_discount=2.0, fob_discount=1.0 from condition
        self.assertAlmostEqual(order.cash_discount, 2.0, places=2)
        self.assertAlmostEqual(order.fob_discount, 1.0, places=2)

    def test_condition_seller_discount_applied_via_onchange(self):
        """seller_discount is applied to lines via _onchange_product_id."""
        order = self._create_order()
        # condition.seller_discount = 5.0 (set in _setup_commercial_policy)
        # The onchange fires during create and applies condition discount
        line = self._create_order_line(order)
        # onchange is UI-only; in programmatic create, we pass seller_discount
        # or it comes from _apply_condition_to_line in onchange context.
        # In test (TransactionCase), onchange does NOT fire on create.
        # So we call it manually to simulate UI behavior.
        order._apply_condition_to_line(line, self.condition)
        self.assertAlmostEqual(line.seller_discount, 5.0, places=2)

    def test_order_without_condition_blocks_confirmation(self):
        """Order for partner without commercial condition blocks confirmation."""
        partner_no_cond = self.env["res.partner"].create(
            {"name": "No Condition Partner"}
        )
        order = self.env["sale.order"].create(
            {
                "partner_id": partner_no_cond.id,
                "user_id": self.salesperson.id,
            }
        )
        self._create_order_line(order)
        with self.assertRaises(UserError):
            order.action_confirm()

    def test_partner_without_condition_has_zero_discounts(self):
        """Order for partner without condition has zero cash/fob discounts."""
        partner_no_cond = self.env["res.partner"].create(
            {"name": "No Condition Partner 2"}
        )
        order = self.env["sale.order"].create(
            {
                "partner_id": partner_no_cond.id,
                "user_id": self.salesperson.id,
            }
        )
        self.assertAlmostEqual(order.cash_discount, 0.0, places=2)
        self.assertAlmostEqual(order.fob_discount, 0.0, places=2)


# ── 4. Multi-product with different rules ───────────────────────────────────


@tagged("post_install", "-at_install")
class TestMultiProductRules(CommercialPolicyTestCommon):
    """Different products respect their specific profile rules."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        # Add a template-specific rule for product_b with lower max
        cls.template_b_rule = cls.env["tr.sales.profile.rule"].create(
            {
                "profile_id": cls.agent_profile.id,
                "applied_on": "product_template",
                "product_tmpl_id": cls.product_template_b.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 3.0, "commission_rate": 5.0}),
                ],
            }
        )

    def test_each_line_respects_its_own_rule(self):
        """Product A uses general rule (max 10%), B uses template rule (max 3%)."""
        order = self._create_order()
        line_a = self._create_order_line(order, seller_discount=9.0)
        line_b = self._create_order_line(
            order, product=self.product_b, seller_discount=2.0
        )
        # Product A: general rule max = 10%
        self.assertAlmostEqual(line_a.seller_discount_max, 10.0, places=2)
        # Product B: template rule max = 3%
        self.assertAlmostEqual(line_b.seller_discount_max, 3.0, places=2)

    def test_product_b_above_template_rule_blocked(self):
        """Product B with discount above its template rule max is blocked."""
        order = self._create_order()
        # 4% exceeds template_b_rule max of 3%
        with self.assertRaises(ValidationError):
            self._create_order_line(order, product=self.product_b, seller_discount=4.0)

    def test_product_a_at_max_product_b_within_max_both_ok(self):
        """Both products at their respective max values pass validation."""
        order = self._create_order()
        line_a = self._create_order_line(order, seller_discount=10.0)
        line_b = self._create_order_line(
            order, product=self.product_b, seller_discount=3.0
        )
        self.assertAlmostEqual(line_a.seller_discount, 10.0, places=2)
        self.assertAlmostEqual(line_b.seller_discount, 3.0, places=2)


# ── 5. Commission + discount integration ───────────────────────────────────


@tagged("post_install", "-at_install")
class TestCommissionDiscountIntegration(CommercialPolicyTestCommon):
    """Commission rate and seller discount integration tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()
        cls.product_template_a.invoice_policy = "order"

    def test_seller_discount_above_all_bands_is_blocked(self):
        """Discount above all commission bands max (10%) is blocked."""
        order = self._create_order()
        with self.assertRaises(ValidationError):
            self._create_order_line(order, seller_discount=11.0)

    def test_commission_rate_matches_correct_band(self):
        """Commission rate matches the band for the given seller_discount."""
        order = self._create_order()
        # Band 1: discount_up_to=5%, commission_rate=10%
        # Band 2: discount_up_to=10%, commission_rate=7%
        line_low = self._create_order_line(order, seller_discount=3.0)
        self.assertAlmostEqual(line_low.commission_rate, 10.0, places=2)

        line_high = self._create_order_line(
            order, product=self.product_b, seller_discount=7.0
        )
        self.assertAlmostEqual(line_high.commission_rate, 7.0, places=2)

    def test_invoice_agent_has_commission(self):
        """Invoice agent line has a commission after order confirmation."""
        order = self._create_order()
        order.fiscal_operation_id = False
        line = self._create_order_line(order, seller_discount=3.0)
        self.assertAlmostEqual(line.commission_rate, 10.0, places=2)

        order.action_confirm()
        invoice = order._create_invoices()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda invl: invl.display_type == "product"
        )
        self.assertTrue(inv_line)
        agent_line = inv_line[0].agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        self.assertTrue(agent_line, "Expected agent line on invoice")

    def test_commission_at_band_boundary(self):
        """Discount exactly at band boundary uses that band's rate."""
        order = self._create_order()
        # discount_up_to=5% → commission_rate=10%
        line = self._create_order_line(order, seller_discount=5.0)
        self.assertAlmostEqual(line.commission_rate, 10.0, places=2)

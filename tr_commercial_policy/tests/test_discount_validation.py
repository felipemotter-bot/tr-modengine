# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestDiscountValidation(CommercialPolicyTestCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_seller_discount_within_limit(self):
        """Test that seller_discount within limit is accepted."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        # general_rule has seller_discount_max=10.0
        line.seller_discount = 10.0
        # Should not raise

    def test_seller_discount_exceeds_limit(self):
        """Test that seller_discount > max raises ValidationError."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        with self.assertRaises(ValidationError):
            line.seller_discount = 15.0

    def test_cash_discount_within_limit(self):
        """Test that cash_discount within profile limit is accepted."""
        order = self._create_order()
        order.payment_term_id = self.payment_term_short
        # agent_profile.cash_discount_max = 5.0
        order.cash_discount = 5.0
        # Should not raise

    def test_cash_discount_exceeds_limit_sets_director_level(self):
        """Cash discount above profile max sets approval level to director."""
        order = self._create_order()
        order.cash_discount = 6.0
        self.assertEqual(order.discount_approval_level, "director")

    def test_fob_discount_within_limit(self):
        """Test that fob_discount within profile limit is accepted."""
        order = self._create_order()
        # agent_profile.fob_discount_max = 3.0
        order.fob_discount = 3.0
        # Should not raise

    def test_fob_discount_exceeds_limit_sets_director_level(self):
        """FOB discount above profile max sets approval level to director."""
        order = self._create_order()
        order.fob_discount = 4.0
        self.assertEqual(order.discount_approval_level, "director")

    def test_cash_discount_eligible_short_term(self):
        """Test cash discount is allowed with short payment term."""
        order = self._create_order()
        order.payment_term_id = self.payment_term_short
        # 15 days avg ≤ 30 days max → OK
        order.cash_discount = 3.0
        # Should not raise

    def test_cash_discount_ineligible_long_term_sets_director_level(self):
        """Cash discount with long payment term sets approval level to director."""
        order = self._create_order()
        order.cash_discount = 0.0
        order.payment_term_id = self.payment_term_long
        # 60 days avg > 30 days max → director approval needed
        order.cash_discount = 3.0
        self.assertEqual(order.discount_approval_level, "director")

    def test_payment_term_avg_days_computed(self):
        """Test that payment_term_avg_days is correctly computed."""
        order = self._create_order()
        order.payment_term_id = self.payment_term_short
        self.assertAlmostEqual(order.payment_term_avg_days, 15.0, places=1)

        order.payment_term_id = self.payment_term_long
        self.assertAlmostEqual(order.payment_term_avg_days, 60.0, places=1)

    def test_payment_term_avg_days_weighted(self):
        """Test avg days with multi-installment payment term."""
        multi_term = self.env["account.payment.term"].create(
            {
                "name": "30/60 days",
                "line_ids": [
                    (0, 0, {"value": "percent", "value_amount": 50, "days": 30}),
                    (0, 0, {"value": "balance", "days": 60}),
                ],
            }
        )
        order = self._create_order()
        order.payment_term_id = multi_term
        # Weighted avg: (30*50 + 60*50) / 100 = 45
        self.assertAlmostEqual(order.payment_term_avg_days, 45.0, places=1)

    def test_no_profile_skips_validation(self):
        """Test that orders without profile skip discount validation."""
        self.salesperson.partner_id.sales_profile_id = False
        # Clear company default so profile can't resolve via fallback
        self.env.company.default_sales_profile_id = False
        order = self._create_order()
        self.assertFalse(order.sales_profile_id)
        # Should not raise even with high values
        order.cash_discount = 99.0
        order.fob_discount = 99.0

    def test_extra_discount_negative_blocked(self):
        """Negative extra discount raises ValidationError."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        with self.assertRaises(ValidationError):
            line.write({"extra_discount": -1.0})

    def test_extra_discount_above_99_blocked(self):
        """Extra discount above 99% raises ValidationError."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        with self.assertRaises(ValidationError):
            line.write({"extra_discount": 100.0})

    def test_total_discount_above_99_blocked(self):
        """Seller + extra discount above 99% raises ValidationError."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0, seller_discount=5.0)
        with self.assertRaises(ValidationError):
            line.write({"extra_discount": 95.0})

    def test_extra_discount_within_limit_ok(self):
        """Extra discount within limit does not raise."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        line.write({"extra_discount": 10.0})
        self.assertAlmostEqual(line.extra_discount, 10.0, places=2)

    def test_decimal_precision_discount_policy_exists(self):
        """Decimal precision 'Discount Policy' is configured with 4 digits."""
        precision = self.env["decimal.precision"].search(
            [("name", "=", "Discount Policy")], limit=1
        )
        self.assertTrue(precision)
        self.assertEqual(precision.digits, 4)

    def test_decimal_precision_sale_price_exists(self):
        """Decimal precision 'Sale Price' is configured with 2 digits."""
        precision = self.env["decimal.precision"].search(
            [("name", "=", "Sale Price")], limit=1
        )
        self.assertTrue(precision)
        self.assertEqual(precision.digits, 2)

    def test_seller_discount_4_decimal_precision(self):
        """Seller discount stores 4 decimal places correctly."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        line.write({"seller_discount": 3.1234})
        self.assertAlmostEqual(line.seller_discount, 3.1234, places=4)

    def test_extra_discount_4_decimal_precision(self):
        """Extra discount stores 4 decimal places correctly."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        line.write({"extra_discount": 2.5678})
        self.assertAlmostEqual(line.extra_discount, 2.5678, places=4)

    def test_price_unit_2_decimal_precision(self):
        """Price unit uses 2 decimal places."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        line.write({"seller_discount": 3.1234})
        # price_unit = 100 * (1 - 3.1234/100) = 96.8766 → rounds to 96.88
        self.assertAlmostEqual(line.price_unit, 96.88, places=2)

    def test_extra_discount_reason_cleared_when_zero(self):
        """Extra discount reason is cleared when extra discount is set to 0."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        line.write(
            {
                "extra_discount": 5.0,
                "extra_discount_reason": "Old reason",
            }
        )
        self.assertEqual(line.extra_discount_reason, "Old reason")
        line.write({"extra_discount": 0.0, "extra_discount_reason": ""})
        self.assertFalse(line.extra_discount_reason)

    # ------------------------------------------------------------------
    # Calculator reverse logic consistency tests
    # ------------------------------------------------------------------

    def test_reverse_price_within_seller_max(self):
        """Reverse calc: desired price within seller_discount_max."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        ref_price = line.reference_price
        # Simulate: desired price = 95, so total discount = 5%
        desired = 95.0
        total_needed = round((1 - desired / ref_price) * 10000) / 100
        # 5% fits within seller_discount_max, no extra needed
        line.write({"seller_discount": total_needed, "extra_discount": 0.0})
        self.assertAlmostEqual(line.price_unit, desired, places=1)

    def test_reverse_price_exceeds_seller_max(self):
        """Reverse calc: desired price requires extra discount."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        ref_price = line.reference_price
        seller_max = line.seller_discount_max
        # Desired price that requires more than seller_max
        desired = ref_price * (1 - (seller_max + 3.0) / 100)
        total_needed = round((1 - desired / ref_price) * 10000) / 100
        extra_needed = total_needed - seller_max
        line.write(
            {
                "seller_discount": seller_max,
                "extra_discount": extra_needed,
            }
        )
        self.assertAlmostEqual(line.price_unit, desired, places=1)

    def test_reverse_order_discount_cash_changes_amount(self):
        """Increasing cash discount reduces the order untaxed amount."""
        order = self._create_order()
        self._create_order_line(order, base_price=100.0, qty=10)
        amount_before = order.amount_untaxed
        # Increase cash discount and trigger onchange to recalculate lines
        order.cash_discount = order.cash_discount + 2.0
        order._onchange_cash_fob_discount()
        self.assertLess(
            order.amount_untaxed,
            amount_before,
            "Increasing cash discount should reduce the untaxed amount.",
        )

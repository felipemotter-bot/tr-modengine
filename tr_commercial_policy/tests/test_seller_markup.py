# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from ..models.policy_utils import calc_price_unit, validate_seller_markup
from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestSellerMarkup(CommercialPolicyTestCommon):
    """Tests for the seller markup (negative seller_discount) feature."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.icp = cls.env["ir.config_parameter"].sudo()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    def _set_markup_max(self, value):
        self.icp.set_param("tr_commercial_policy.seller_markup_max_pct", str(value))

    def tearDown(self):
        super().tearDown()
        # Reset markup limit after each test
        self.icp.set_param("tr_commercial_policy.seller_markup_max_pct", "0.0")

    # --- validate_seller_markup helper ---

    def test_validate_markup_zero_limit_rejects_negative(self):
        """Negative discount is rejected when markup_max is 0 (default)."""
        self._set_markup_max(0.0)
        with self.assertRaises(ValidationError):
            validate_seller_markup(self.env, -1.0)

    def test_validate_markup_within_limit_accepted(self):
        """Negative discount within limit is accepted without error."""
        self._set_markup_max(5.0)
        validate_seller_markup(self.env, -5.0)  # exactly at limit — no exception

    def test_validate_markup_boundary_exact(self):
        """-markup_max exactly is the allowed floor (no error)."""
        self._set_markup_max(3.0)
        validate_seller_markup(self.env, -3.0)

    def test_validate_markup_exceeds_limit_raises(self):
        """Discount below -markup_max raises ValidationError."""
        self._set_markup_max(3.0)
        with self.assertRaises(ValidationError):
            validate_seller_markup(self.env, -3.01)

    def test_validate_markup_zero_discount_always_accepted(self):
        """Zero seller_discount is always accepted regardless of limit."""
        self._set_markup_max(0.0)
        validate_seller_markup(self.env, 0.0)

    # --- calc_price_unit with negative discount ---

    def test_calc_price_unit_markup_raises_price(self):
        """Negative seller_discount produces price_unit > reference_price."""
        result = calc_price_unit(100.0, -5.0, 0.0)
        self.assertAlmostEqual(result, 105.0, places=2)

    def test_calc_price_unit_zero_discount(self):
        """Zero discount produces price_unit == reference_price."""
        result = calc_price_unit(100.0, 0.0, 0.0)
        self.assertAlmostEqual(result, 100.0, places=2)

    # --- sale.order.line validation ---

    def test_sale_line_markup_within_limit_accepted(self):
        """Sale line with markup within global limit is accepted."""
        self._set_markup_max(5.0)
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=-3.0)
        self.assertAlmostEqual(line.seller_discount, -3.0, places=4)

    def test_sale_line_markup_exceeds_limit_raises(self):
        """Sale line with markup above global limit raises ValidationError."""
        self._set_markup_max(2.0)
        order = self._create_order()
        with self.assertRaises(ValidationError):
            self._create_order_line(order, seller_discount=-3.0)

    def test_sale_line_markup_no_limit_raises(self):
        """Sale line with any markup raises ValidationError when limit is 0."""
        self._set_markup_max(0.0)
        order = self._create_order()
        with self.assertRaises(ValidationError):
            self._create_order_line(order, seller_discount=-0.1)

    def test_sale_line_price_unit_above_reference_with_markup(self):
        """price_unit > reference_price when seller_discount is negative."""
        self._set_markup_max(10.0)
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=-5.0)
        self.assertGreater(line.price_unit, line.reference_price)

    # --- commission clamping for markup ---

    def test_commission_clamp_markup_uses_zero_band(self):
        """Commission with negative seller_discount falls in the zero-discount band."""
        self._set_markup_max(10.0)
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=-5.0)
        # band_low: discount_up_to=5, rate=10.0 — negative clamped to 0 → this band
        self.assertEqual(line.commission_rate, 10.0)

    # --- account.move.line validation ---

    def test_invoice_line_markup_within_limit_accepted(self):
        """Invoice line with markup within limit is accepted."""
        self._set_markup_max(5.0)
        invoice = self._create_manual_invoice()
        line = self.env["account.move.line"].create(
            {
                "move_id": invoice.id,
                "product_id": self.product_a.id,
                "quantity": 1,
                "seller_discount": -3.0,
            }
        )
        self.assertAlmostEqual(line.seller_discount, -3.0, places=4)

    def test_invoice_line_markup_exceeds_limit_raises(self):
        """Invoice line with markup above limit raises ValidationError."""
        self._set_markup_max(2.0)
        invoice = self._create_manual_invoice()
        with self.assertRaises(ValidationError):
            self.env["account.move.line"].create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_a.id,
                    "quantity": 1,
                    "seller_discount": -3.0,
                }
            )

    def test_invoice_line_commission_clamp_markup_uses_zero_band(self):
        """Invoice line commission with markup falls in zero-discount band."""
        self._set_markup_max(10.0)
        _, invoice = self._create_confirmed_order_with_invoice(seller_discount=-5.0)
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        self.assertEqual(inv_line.commission_rate, 10.0)

    # --- partner.commercial.condition.line constraint ---

    def test_condition_line_markup_exceeds_limit_raises(self):
        """PartnerCommercialConditionLine with markup above limit raises."""
        self._set_markup_max(2.0)
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition.line"].create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product_template",
                    "product_tmpl_id": self.product_template_a.id,
                    "seller_discount": -5.0,
                }
            )

    def test_condition_line_markup_with_extra_discount_raises(self):
        """PartnerCommercialConditionLine with markup + extra_discount raises."""
        self._set_markup_max(10.0)
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition.line"].create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product_template",
                    "product_tmpl_id": self.product_template_a.id,
                    "seller_discount": -3.0,
                    "extra_discount": 2.0,
                }
            )

    def test_condition_line_markup_within_limit_accepted(self):
        """PartnerCommercialConditionLine with markup within limit is accepted."""
        self._set_markup_max(10.0)
        line = self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product_template",
                "product_tmpl_id": self.product_template_a.id,
                "seller_discount": -5.0,
            }
        )
        self.assertAlmostEqual(line.seller_discount, -5.0, places=4)

    # --- markup + extra_discount combination blocked ---

    def test_sale_line_markup_with_extra_discount_raises(self):
        """Combining negative seller_discount with extra_discount raises."""
        self._set_markup_max(10.0)
        order = self._create_order()
        with self.assertRaises(ValidationError):
            self._create_order_line(order, seller_discount=-3.0, extra_discount=2.0)

    def test_invoice_line_markup_with_extra_discount_raises(self):
        """Invoice line with markup + extra_discount raises."""
        self._set_markup_max(10.0)
        invoice = self._create_manual_invoice()
        with self.assertRaises(ValidationError):
            self.env["account.move.line"].create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_a.id,
                    "quantity": 1,
                    "seller_discount": -3.0,
                    "extra_discount": 2.0,
                }
            )

    # --- seller_markup_max computed field ---

    def test_seller_markup_max_field_reflects_config(self):
        """seller_markup_max computed field on sale line matches config param."""
        self._set_markup_max(7.5)
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=0.0)
        self.assertAlmostEqual(line.seller_markup_max, 7.5, places=2)

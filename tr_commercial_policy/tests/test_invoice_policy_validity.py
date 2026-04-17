# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestInvoicePolicyValidity(CommercialPolicyTestCommon):
    """Tests for invoice temporal validity (Phase 4).

    Verifies that invoices outside the configured validity period
    lose parity and enter own-rules revalidation.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()
        cls.product_template_a.invoice_policy = "order"

    def _create_old_order_with_invoice(self, days_ago=100):
        """Create a confirmed order dated `days_ago` and its invoice."""
        order = self._create_order()
        order.fiscal_operation_id = False
        line = self._create_order_line(order, qty=10)
        line.seller_discount = 5.0
        line.extra_discount = 0.0
        order.action_confirm()
        # Backdate the order confirmation
        old_date = fields.Datetime.now() - timedelta(days=days_ago)
        order.write({"date_order": old_date})
        invoice = order._create_invoices()
        return order, invoice

    # --- Configuration ---

    def test_validity_disabled_when_zero(self):
        """validity_days=0 → feature disabled, old invoice posts free."""
        self.env.company.invoice_validity_days = 0
        _order, invoice = self._create_old_order_with_invoice(days_ago=999)
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    def test_negative_validity_days_rejected(self):
        """Negative validity days → ValidationError."""
        with self.assertRaises(ValidationError):
            self.env.company.invoice_validity_days = -1

    def test_settings_company_related_works(self):
        """Settings field reads/writes company field correctly."""
        self.env.company.invoice_validity_days = 42
        settings = self.env["res.config.settings"].create({})
        self.assertEqual(settings.company_invoice_validity_days, 42)
        settings.company_invoice_validity_days = 99
        settings.set_values()
        self.assertEqual(self.env.company.invoice_validity_days, 99)

    # --- Within validity ---

    def test_within_validity_posts_free(self):
        """Invoice within validity → posts free (parity)."""
        self.env.company.invoice_validity_days = 90
        _order, invoice = self._create_old_order_with_invoice(days_ago=30)
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    def test_within_validity_boundary(self):
        """Invoice exactly at deadline boundary → posts free."""
        self.env.company.invoice_validity_days = 30
        _order, invoice = self._create_old_order_with_invoice(days_ago=30)
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    # --- Outside validity ---

    def test_outside_validity_enters_own_rules(self):
        """Invoice outside validity, values ok → posts via own rules."""
        self.env.company.invoice_validity_days = 30
        _order, invoice = self._create_old_order_with_invoice(days_ago=60)
        # Values are within profile limits → own rules pass → posts
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    def test_outside_validity_with_violation_triggers_tier(self):
        """Invoice outside validity + profile violation → tier.

        Isolation: the sale line and invoice line both have extra_discount=3,
        so there is NO snapshot divergence.  The tier is triggered purely by
        the profile limit (manager_extra_limit=5 → level "manager").
        Without the validity gate the invoice would enter snapshot checks,
        find parity, and post freely — so the test only passes when the
        validity branch redirects the flow to own-rules → tier.
        """
        self.env.company.invoice_validity_days = 30
        _order, invoice = self._create_old_order_with_invoice(days_ago=60)
        profile = invoice.sales_profile_id
        # Set extra_discount on BOTH sale and invoice lines to keep parity.
        # Must be done after confirm to avoid tier blocking confirmation.
        sale_line = _order.order_line.filtered(lambda line: line.product_id)
        sale_line.extra_discount = 3.0
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        inv_line.with_context(skip_invoice_sync=True, check_move_validity=False).write(
            {"extra_discount": 3.0}
        )
        # Profile limit: 3.0 <= 5.0 → "manager" tier, no snapshot divergence
        profile.manager_extra_limit = 5.0
        invoice.invalidate_recordset(["discount_approval_level"])
        self.assertEqual(invoice.discount_approval_level, "manager")
        # action_post enters own-rules via validity expiry path
        # Tier validation blocks posting (need_validation = True)
        with self.assertRaises(UserError):
            invoice.action_post()

    def test_outside_validity_bypasses_snapshot_hard_block(self):
        """Invoice outside validity + Classe B divergence → NOT hard block.

        This is the key test: expired invoice bypasses snapshot checks
        entirely and enters own-rules (like manual invoice).
        """
        self.env.company.invoice_validity_days = 30
        _order, invoice = self._create_old_order_with_invoice(days_ago=60)
        # Create Classe B divergence (base_price)
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        inv_line.with_context(skip_invoice_sync=True, check_move_validity=False).write(
            {"base_price": 999.0}
        )
        # Expired → bypasses snapshot → own rules → posts
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    # --- Multi-order ---

    def test_multi_order_uses_oldest_date(self):
        """Multi-order invoice uses oldest date_order for validity."""
        self.env.company.invoice_validity_days = 45
        # Order 1: old (60 days ago)
        order1 = self._create_order()
        order1.fiscal_operation_id = False
        line1 = self._create_order_line(order1, qty=10)
        line1.seller_discount = 5.0
        line1.extra_discount = 0.0
        order1.action_confirm()
        order1.write({"date_order": fields.Datetime.now() - timedelta(days=60)})
        # Order 2: recent (10 days ago)
        order2 = self._create_order()
        order2.fiscal_operation_id = False
        line2 = self._create_order_line(order2, qty=5)
        line2.seller_discount = 5.0
        line2.extra_discount = 0.0
        order2.action_confirm()
        order2.write({"date_order": fields.Datetime.now() - timedelta(days=10)})
        invoice = (order1 | order2)._create_invoices()
        # Oldest is 60 days, validity is 45 → expired
        self.assertFalse(invoice._is_within_validity())

    # --- Effective date ---

    def test_effective_date_uses_invoice_date(self):
        """invoice_date is the primary effective date."""
        self.env.company.invoice_validity_days = 30
        order, invoice = self._create_old_order_with_invoice(days_ago=60)
        deadline = fields.Date.to_date(order.date_order) + timedelta(days=30)
        # invoice_date at deadline → within validity
        invoice.invoice_date = deadline
        self.assertTrue(invoice._is_within_validity())
        # invoice_date past deadline → outside
        invoice.invoice_date = deadline + timedelta(days=1)
        self.assertFalse(invoice._is_within_validity())

    def test_effective_date_falls_back_to_date(self):
        """When invoice_date is empty, uses date field."""
        self.env.company.invoice_validity_days = 30
        order, invoice = self._create_old_order_with_invoice(days_ago=60)
        deadline = fields.Date.to_date(order.date_order) + timedelta(days=30)
        invoice.invoice_date = False
        invoice.date = deadline
        self.assertTrue(invoice._is_within_validity())
        invoice.date = deadline + timedelta(days=1)
        self.assertFalse(invoice._is_within_validity())

    def test_effective_date_falls_back_to_today(self):
        """When both invoice_date and date are empty, uses today."""
        self.env.company.invoice_validity_days = 30
        _order, invoice = self._create_old_order_with_invoice(days_ago=60)
        invoice.invoice_date = False
        invoice.date = False
        # Today is ~60 days after order, deadline is 30 → outside
        self.assertFalse(invoice._is_within_validity())

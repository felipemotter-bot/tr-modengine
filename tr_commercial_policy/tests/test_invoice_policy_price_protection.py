# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from ..models.policy_utils import (
    calc_price_unit,
    calc_reference_price,
    get_policy_rates,
)
from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestInvoicePolicyPriceProtection(CommercialPolicyTestCommon):
    """Phase 5 — Price protection by product category.

    Tests the write-time guard on account.move.line that prevents direct
    editing of price_unit and discount on out_invoice lines, with
    exceptions for allowed categories, admin, and context flag.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        # Category that allows manual price edit
        cls.categ_allowed = cls.env["product.category"].create(
            {
                "name": "Allowed Category",
                "allow_manual_price_edit": True,
            }
        )
        # Product in the allowed category
        cls.product_allowed = (
            cls.env["product.template"]
            .create(
                {
                    "name": "Product Allowed",
                    "type": "consu",
                    "list_price": 150.0,
                    "categ_id": cls.categ_allowed.id,
                }
            )
            .product_variant_ids[0]
        )
        # Billing user — has invoice access but not director/admin
        cls.billing_user = cls.env["res.users"].create(
            {
                "name": "Test Billing",
                "login": "test_billing_tcp",
                "groups_id": [
                    (4, cls.env.ref("account.group_account_invoice").id),
                ],
            }
        )

    def _create_invoice_with_line(self, product=None, move_type="out_invoice"):
        """Create a minimal invoice with one product line."""
        product = product or self.product_a
        invoice = self.env["account.move"].create(
            {
                "move_type": move_type,
                "partner_id": self.customer.id,
            }
        )
        invoice.with_context(
            tr_skip_price_protection=True,
            check_move_validity=False,
        ).write(
            {
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": product.id,
                            "quantity": 1,
                            "price_unit": 100.0,
                        },
                    )
                ]
            }
        )
        return invoice

    def _get_product_line(self, invoice):
        """Return the first product line from the invoice."""
        return invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product" and line.product_id
        )[:1]

    def _create_manual_invoice_with_condition(self):
        """Create manual invoice with commercial condition applied."""
        invoice = self._create_manual_invoice()
        condition = self.condition
        write_vals = {
            "commercial_condition_id": condition.id,
            "sales_profile_id": condition.applicable_profile_id.id,
            "tr_cash_discount": condition.cash_discount,
            "tr_fob_discount": condition.fob_discount,
            "tr_contractual_return": condition.contractual_return,
        }
        if condition.payment_term_id:  # pragma: no cover
            write_vals["invoice_payment_term_id"] = (  # pragma: no cover
                condition.payment_term_id.id
            )
        invoice.write(write_vals)
        return invoice

    def _add_product_line(self, invoice, product=None):
        """Add a product line to a manual invoice with proper policy values."""
        product = product or self.product_a
        condition = invoice.commercial_condition_id

        seller_disc, extra_disc, _source = condition._resolve_discount_for_product(
            product
        )
        pricelist = condition.pricelist_id
        move_date = (
            invoice.invoice_date or invoice.date or fields.Date.context_today(invoice)
        )
        raw = pricelist._get_product_price(product, 1, date=move_date)
        base_price = product._get_tax_included_unit_price(
            invoice.company_id,
            invoice.currency_id,
            move_date,
            "sale",
            fiscal_position=invoice.fiscal_position_id,
            product_price_unit=raw,
            product_currency=pricelist.currency_id,
        )
        tax_rate, freight_rate, admin_rate = get_policy_rates(self.env)
        ref_price = calc_reference_price(
            base_price,
            invoice.tr_contractual_return,
            tax_rate,
            freight_rate,
            admin_rate,
        )
        price_unit = calc_price_unit(ref_price, seller_disc, extra_disc)
        discount = (invoice.tr_cash_discount or 0) + (invoice.tr_fob_discount or 0)
        line_vals = {
            "product_id": product.id,
            "quantity": 1,
            "price_unit": price_unit,
            "seller_discount": seller_disc,
            "extra_discount": extra_disc,
            "base_price": base_price,
            "reference_price": ref_price,
            "discount": discount,
        }
        lines_before = invoice.invoice_line_ids
        invoice.with_context(
            tr_skip_price_protection=True,
            tr_skip_manual_snapshot=True,
        ).write({"line_ids": [Command.create(line_vals)]})
        line = (invoice.invoice_line_ids - lines_before).filtered(
            lambda inv_line: inv_line.product_id == product
        )[:1]
        rate = line._get_commission_rate_for_discount(seller_disc)
        if rate is not False:
            invoice.with_context(
                tr_skip_price_protection=True,
                tr_skip_manual_snapshot=True,
            ).write({"line_ids": [Command.update(line.id, {"commission_rate": rate})]})
        return line

    # --- Test 1: Protected category blocks price_unit edit ---

    def test_protected_category_blocks_price_unit(self):
        """out_invoice, protected category: direct price_unit write → blocked."""
        invoice = self._create_invoice_with_line(product=self.product_a)
        line = self._get_product_line(invoice)
        with self.assertRaises(ValidationError):
            line.with_user(self.billing_user).write({"price_unit": 999.0})

    # --- Test 2: Protected category blocks discount edit ---

    def test_protected_category_blocks_discount(self):
        """out_invoice, protected category: direct discount write → blocked."""
        invoice = self._create_invoice_with_line(product=self.product_a)
        line = self._get_product_line(invoice)
        with self.assertRaises(ValidationError):
            line.with_user(self.billing_user).write({"discount": 50.0})

    # --- Test 3: Sale-origin line also protected ---

    def test_sale_origin_line_protected(self):
        """out_invoice from sale, protected category: price_unit write → blocked."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        with self.assertRaises(ValidationError):
            inv_line.with_user(self.billing_user).write({"price_unit": 999.0})

    # --- Test 4: Allowed category permits price_unit ---

    def test_allowed_category_permits_price_unit(self):
        """Category with allow_manual_price_edit=True: price_unit edit → OK."""
        invoice = self._create_invoice_with_line(product=self.product_allowed)
        line = self._get_product_line(invoice)
        line.with_user(self.billing_user).write({"price_unit": 999.0})
        self.assertAlmostEqual(line.price_unit, 999.0)

    # --- Test 5: Allowed category permits discount ---

    def test_allowed_category_permits_discount(self):
        """Category with allow_manual_price_edit=True: discount edit → OK."""
        invoice = self._create_invoice_with_line(product=self.product_allowed)
        line = self._get_product_line(invoice)
        line.with_user(self.billing_user).with_context(check_move_validity=False).write(
            {"discount": 15.0}
        )
        self.assertAlmostEqual(line.discount, 15.0)

    # --- Test 6: Admin bypasses protection ---

    def test_admin_bypasses_protection(self):
        """Admin user can edit price_unit even on protected category."""
        invoice = self._create_invoice_with_line(product=self.product_a)
        line = self._get_product_line(invoice)
        line.with_user(self.env.ref("base.user_admin")).write({"price_unit": 999.0})
        self.assertAlmostEqual(line.price_unit, 999.0)

    # --- Test 7: Context flag bypasses protection ---

    def test_context_flag_bypasses_protection(self):
        """Context tr_skip_price_protection bypasses the guard."""
        invoice = self._create_invoice_with_line(product=self.product_a)
        line = self._get_product_line(invoice)
        line.with_user(self.billing_user).with_context(
            tr_skip_price_protection=True
        ).write({"price_unit": 999.0})
        self.assertAlmostEqual(line.price_unit, 999.0)

    # --- Test 8: Section/note lines not protected ---

    def test_section_line_not_protected(self):
        """Section/note lines skip the guard even on protected category."""
        invoice = self._create_invoice_with_line(product=self.product_a)
        # Add a section line
        invoice.with_context(
            tr_skip_price_protection=True, check_move_validity=False
        ).write(
            {"line_ids": [(0, 0, {"display_type": "line_section", "name": "Section"})]}
        )
        section = invoice.line_ids.filtered(
            lambda line: line.display_type == "line_section"
        )[:1]
        # Writing price_unit on section should NOT raise (skips guard)
        section.with_user(self.billing_user).with_context(
            check_move_validity=False
        ).write({"price_unit": 0.0})

    # --- Test 9: out_refund not protected ---

    def test_out_refund_not_protected(self):
        """out_refund: no price protection even with protected category."""
        invoice = self._create_invoice_with_line(
            product=self.product_a, move_type="out_refund"
        )
        line = self._get_product_line(invoice)
        line.with_user(self.billing_user).write({"price_unit": 999.0})
        self.assertAlmostEqual(line.price_unit, 999.0)

    # --- Test 9: Post-time fallback blocks tampered manual invoice ---

    def test_post_fallback_blocks_tampered_price(self):
        """Manual invoice, protected category, price_unit tampered via
        bypass → action_post still blocks."""
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice, product=self.product_a)
        # Tamper price_unit via bypass
        line.with_context(
            tr_skip_price_protection=True, check_move_validity=False
        ).write({"price_unit": 0.01})
        with self.assertRaises(UserError):
            invoice.action_post()

    # --- Test 10: Post-time fallback respects allowed category ---

    def test_post_fallback_respects_allowed_category(self):
        """Manual invoice, allowed category, price_unit tampered via
        bypass → action_post allows posting."""
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice, product=self.product_allowed)
        # Tamper price_unit via bypass — allowed category should let it post
        line.with_context(
            tr_skip_price_protection=True, check_move_validity=False
        ).write({"price_unit": 0.01})
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    # --- Test 11: Allowed category does NOT bypass sale-origin lines ---

    def test_allowed_category_blocked_on_sale_origin(self):
        """allow_manual_price_edit on category does NOT bypass the guard
        for invoice lines that originate from a sale order."""
        self.product_template_a.categ_id = self.categ_allowed
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        # Sale-origin line: category exception does NOT apply
        with self.assertRaises(ValidationError):
            inv_line.with_user(self.billing_user).write({"price_unit": 999.0})
        self.product_template_a.categ_id = self.categ_chemicals

    # --- Test 12: Category flag does NOT affect sale.order.line ---

    def test_category_flag_does_not_affect_sale_order(self):
        """allow_manual_price_edit on category does NOT bypass sale.order.line
        price protection — the exception is invoice-only."""
        self.product_template_a.categ_id = self.categ_allowed
        order = self._create_order()
        order.fiscal_operation_id = False
        line = self._create_order_line(order, qty=1)
        with self.assertRaises(ValidationError):
            line.with_user(self.salesperson).write({"price_unit": 999.0})
        # Restore category
        self.product_template_a.categ_id = self.categ_chemicals

    # --- Test 13: Fiscal document import bypasses guard ---

    def test_import_fiscal_document_bypasses_guard(self):
        """import_fiscal_document propagates tr_skip_price_protection
        so l10n_br_account can write price_unit without triggering guard."""
        from odoo.addons.l10n_br_account.models.account_move import (
            AccountMove as L10nBrAccountMove,
        )
        from unittest.mock import patch

        captured_ctx = {}

        def spy_import(spy_self, fiscal_doc, move_id=None, move_type="in_invoice"):
            captured_ctx.update(spy_self.env.context)
            return spy_self.env["account.move"]

        with patch.object(L10nBrAccountMove, "import_fiscal_document", spy_import):
            self.env["account.move"].import_fiscal_document(
                self.env["l10n_br_fiscal.document"],
                move_type="out_invoice",
            )
        self.assertTrue(captured_ctx.get("tr_skip_price_protection"))

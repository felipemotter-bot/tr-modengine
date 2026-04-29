# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestAccountInvoiceLineAgentCoverage(CommercialPolicyTestCommon):
    """Cover account_invoice_line_agent.py gaps."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()
        # Invoice requires ordered quantity policy
        cls.product_template_a.invoice_policy = "order"

    def _create_confirmed_order_with_invoice(self):
        """Create confirmed order and generate invoice from it."""
        order = self._create_order()
        order.fiscal_operation_id = False
        line = self._create_order_line(order, seller_discount=3.0)
        order.action_confirm()
        invoice = order._create_invoices()
        return order, line, invoice

    def test_invoice_line_agent_exists(self):
        """Invoice line has agent commission after order confirmation."""
        _order, sol, invoice = self._create_confirmed_order_with_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )
        self.assertTrue(inv_line, "Expected at least one product invoice line")
        inv_line = inv_line[0]
        agent_line = inv_line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        self.assertTrue(agent_line, "Expected agent line on invoice")


@tagged("post_install", "-at_install")
class TestResPartnerIsSalesDirector(CommercialPolicyTestCommon):
    """Cover res_partner.py is_sales_director gaps."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    def test_is_sales_director_true_for_director(self):
        """is_sales_director is True when current user is in director group."""
        partner = self.customer.with_user(self.director_user)
        self.assertTrue(partner.is_sales_director)

    def test_is_sales_director_false_for_salesperson(self):
        """is_sales_director is False when current user is not a director."""
        partner = self.customer.with_user(self.salesperson)
        self.assertFalse(partner.is_sales_director)


@tagged("post_install", "-at_install")
class TestResConfigSettingsCoverage(CommercialPolicyTestCommon):
    """Cover res_config_settings.py behavior."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_default_get_reads_company_related_sales_profile(self):
        """Settings default_get must not classify the field as a default_* field."""
        defaults = self.env["res.config.settings"].default_get(
            ["company_default_sales_profile_id"]
        )
        self.assertIsInstance(defaults, dict)

        settings = self.env["res.config.settings"].new({})
        self.assertEqual(
            settings.company_default_sales_profile_id,
            self.env.company.default_sales_profile_id,
        )

    def test_settings_field_domain_references_company_id_not_id(self):
        """Settings ``company_default_sales_profile_id`` overrides the
        inherited domain to reference ``company_id`` instead of ``id``.

        Regression: the default for ``res.company.default_sales_profile_id``
        is ``[('company_id', '=', id)]``. In ``res.config.settings``
        (TransientModel), ``id`` evaluates to the transient record's id,
        which is never a company id, leaving the dropdown empty. The
        override must reference the ``company_id`` Many2one of the
        settings record so the domain resolves to the company being
        configured.
        """
        field = self.env["res.config.settings"]._fields[
            "company_default_sales_profile_id"
        ]
        self.assertEqual(field.domain, "[('company_id', '=', company_id)]")


@tagged("post_install", "-at_install")
class TestSalesProfileBandsSummary(CommercialPolicyTestCommon):
    """Cover sales_profile.py bands_summary gaps."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_internal_policy()

    def test_bands_summary_agent_profile(self):
        """bands_summary for agent profile rule shows commission bands format."""
        summary = self.general_rule.bands_summary
        self.assertIsNotNone(summary)
        self.assertIn("Disc.", summary)
        self.assertIn("Comm.", summary)
        # Both bands present
        self.assertIn("→", summary)
        self.assertEqual(summary.count("→"), 2)

    def test_bands_summary_internal_profile(self):
        """bands_summary for internal profile rule shows order value bands format."""
        summary = self.internal_general_rule.bands_summary
        self.assertIsNotNone(summary)
        self.assertIn("Min.", summary)
        self.assertIn("Disc. max", summary)
        # Three bands present
        self.assertEqual(summary.count("→"), 3)


@tagged("post_install", "-at_install")
class TestSaleOrderLineAgentCommissionFree(CommercialPolicyTestCommon):
    """Cover sale_order_line_agent.py gaps."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    def test_sale_line_agent_commission_free_returns_zero(self):
        """Commission amount is 0 for commission_free product."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        self.assertEqual(line.commission_rate, 10.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        self.assertTrue(agent_line, "Expected agent line on sale order line")
        # Temporarily mark the product as commission_free
        line.product_id.commission_free = True
        try:
            commission = agent_line.commission_id
            result = agent_line._get_commission_amount(
                commission,
                line.price_subtotal,
                line.product_id,
                line.product_uom_qty,
            )
            self.assertEqual(result, 0.0)
        finally:
            line.product_id.commission_free = False


@tagged("post_install", "-at_install")
class TestSaleOrderLineCoverage(CommercialPolicyTestCommon):
    """Cover sale_order_line.py gaps."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        # Enable order-quantity invoicing so lines become fully invoiceable
        cls.product_template_a.invoice_policy = "order"
        cls.product_template_b.invoice_policy = "order"

    def test_compute_base_price_no_product(self):
        """_compute_base_price returns 0 when line has no product."""
        order = self._create_order()
        # Create a section line — no product_id
        line = self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "name": "Section Header",
                "display_type": "line_section",
            }
        )
        # base_price should be 0 (no product)
        self.assertEqual(line.base_price, 0.0)

    def test_compute_reference_price_denominator_lte_zero(self):
        """_compute_reference_price falls back to base when denominator <= 0."""
        # tax_rate=0.10, freight_rate=0.05, admin_rate=0.02 → den = 1 - cr - 0.17
        # For denominator <= 0: cr >= 0.83 → set contractual_return = 90%
        order = self._create_order()
        order.contractual_return = 90.0  # den = 1 - 0.90 - 0.17 = -0.07
        line = self._create_order_line(order, base_price=100.0)
        # Should fall back to base_price
        self.assertAlmostEqual(line.reference_price, line.base_price, places=2)

    def _create_confirmed_invoiced_line(self):
        """Create confirmed order + invoice, return SO line with qty_invoiced."""
        order = self._create_order()
        order.fiscal_operation_id = False
        line = self._create_order_line(order, seller_discount=5.0, base_price=100.0)
        order.action_confirm()
        order._create_invoices()
        # qty_invoiced is now > 0 because invoice was created with "order" policy
        self.assertGreater(line.qty_invoiced, 0, "Setup: qty_invoiced should be > 0")
        return order, line

    def test_compute_price_unit_skipped_when_qty_invoiced_gt_zero(self):
        """_compute_price_unit skips recompute when qty_invoiced > 0."""
        _order, line = self._create_confirmed_invoiced_line()
        price_before = line.price_unit
        # Trigger the override — price_unit must not change because qty_invoiced > 0
        line._compute_price_unit()
        self.assertAlmostEqual(line.price_unit, price_before, places=2)

    def test_recompute_price_unit_skipped_when_qty_invoiced_gt_zero(self):
        """_recompute_price_unit_from_policy skips line when qty_invoiced > 0."""
        _order, line = self._create_confirmed_invoiced_line()
        price_before = line.price_unit
        # Call the method directly — should skip because qty_invoiced > 0
        line._recompute_price_unit_from_policy()
        self.assertAlmostEqual(line.price_unit, price_before, places=2)

    def test_get_applicable_rule_no_product_returns_empty(self):
        """_get_applicable_rule returns empty recordset when line has no product."""
        order = self._create_order()
        line = self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "name": "Section Header",
                "display_type": "line_section",
            }
        )
        rule = line._get_applicable_rule()
        self.assertFalse(rule)

    def test_get_commission_rate_from_bands_discount_exceeds_all_bands(self):
        """_get_commission_rate_for_discount returns False when no band matches."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=0.0)
        # Use helper directly — discount 15% is beyond all bands (up_to 5% and 10%)
        rate = line._get_commission_rate_for_discount(15.0)
        self.assertIs(rate, False)


@tagged("post_install", "-at_install")
class TestDiscountChangedField(CommercialPolicyTestCommon):
    """Cover _compute_discount_changed on sale.order.line."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_discount_changed_false_when_matches_condition(self):
        """discount_changed is False when seller_discount matches condition."""
        order = self._create_order()
        # Condition has seller_discount=5.0 (set in _setup_commercial_policy)
        line = self._create_order_line(order, seller_discount=5.0)
        self.assertFalse(line.discount_changed)

    def test_discount_changed_true_when_differs(self):
        """discount_changed is True when seller_discount differs from condition."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        self.assertTrue(line.discount_changed)

    def test_discount_changed_false_no_condition(self):
        """discount_changed is False when order has no commercial condition."""
        self.customer.commercial_condition_id = False
        order = self._create_order()
        # No condition → no profile → seller_discount_max is 0, so use 0.0
        line = self._create_order_line(order, seller_discount=0.0)
        self.assertFalse(line.discount_changed)

    def test_discount_changed_false_no_product(self):
        """discount_changed is False when line has no product."""
        order = self._create_order()
        line = self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "name": "Section",
                "display_type": "line_section",
            }
        )
        self.assertFalse(line.discount_changed)


@tagged("post_install", "-at_install")
class TestActionClearExtraDiscount(CommercialPolicyTestCommon):
    """Cover action_clear_extra_discount on sale.order.line."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_clear_extra_discount(self):
        """action_clear_extra_discount zeroes extra_discount and reason."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=5.0)
        line.write({"extra_discount": 3.0, "extra_discount_reason": "Promo"})
        self.assertEqual(line.extra_discount, 3.0)
        line.action_clear_extra_discount()
        self.assertEqual(line.extra_discount, 0.0)
        self.assertFalse(line.extra_discount_reason)


@tagged("post_install", "-at_install")
class TestActionOpenExtraDiscountWizard(CommercialPolicyTestCommon):
    """Cover action_open_extra_discount_wizard on sale.order.line."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_open_wizard_returns_action(self):
        """action_open_extra_discount_wizard returns act_window for wizard."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=5.0)
        line.write({"extra_discount": 2.0, "extra_discount_reason": "Test"})
        result = line.action_open_extra_discount_wizard()
        self.assertEqual(result["type"], "ir.actions.act_window")
        self.assertEqual(result["res_model"], "tr.extra.discount.wizard")
        wizard = self.env["tr.extra.discount.wizard"].browse(result["res_id"])
        self.assertEqual(wizard.sale_line_id, line)
        self.assertAlmostEqual(wizard.extra_discount, 2.0)
        self.assertEqual(wizard.extra_discount_reason, "Test")


@tagged("post_install", "-at_install")
class TestExtraDiscountWizard(CommercialPolicyTestCommon):
    """Cover ExtraDiscountWizard.action_apply validation logic."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def _create_wizard(self, line, discount=0.0, reason=""):
        return self.env["tr.extra.discount.wizard"].create(
            {
                "sale_line_id": line.id,
                "extra_discount": discount,
                "extra_discount_reason": reason,
            }
        )

    def test_apply_negative_discount_raises(self):
        """action_apply raises ValidationError for negative discount."""
        order = self._create_order()
        line = self._create_order_line(order)
        wizard = self._create_wizard(line, discount=-1.0)
        with self.assertRaises(ValidationError):
            wizard.action_apply()

    def test_apply_positive_discount_without_reason_raises(self):
        """action_apply raises when discount > 0 and reason is empty."""
        order = self._create_order()
        line = self._create_order_line(order)
        wizard = self._create_wizard(line, discount=2.0, reason="")
        with self.assertRaises(ValidationError):
            wizard.action_apply()

    def test_apply_positive_discount_with_reason_succeeds(self):
        """action_apply writes discount and reason to line."""
        order = self._create_order()
        line = self._create_order_line(order)
        wizard = self._create_wizard(line, discount=2.0, reason="Promo")
        result = wizard.action_apply()
        self.assertEqual(result["type"], "ir.actions.act_window_close")
        self.assertAlmostEqual(line.extra_discount, 2.0)
        self.assertEqual(line.extra_discount_reason, "Promo")

    def test_apply_zero_discount_without_reason_succeeds(self):
        """action_apply allows zero discount without a reason."""
        order = self._create_order()
        line = self._create_order_line(order)
        line.write({"extra_discount": 2.0, "extra_discount_reason": "Old reason"})
        wizard = self._create_wizard(line, discount=0.0, reason="")
        wizard.action_apply()
        self.assertAlmostEqual(line.extra_discount, 0.0)
        self.assertFalse(line.extra_discount_reason)


@tagged("post_install", "-at_install")
class TestCheckDirectPriceEdit(CommercialPolicyTestCommon):
    """Cover _check_direct_price_edit on sale.order.line."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_direct_price_unit_edit_raises(self):
        """Writing price_unit directly raises ValidationError for non-admin."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=5.0)
        with self.assertRaises(ValidationError):
            line.with_user(self.salesperson).write({"price_unit": 50.0})

    def test_direct_discount_edit_raises(self):
        """Writing discount directly raises ValidationError for non-admin."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=5.0)
        with self.assertRaises(ValidationError):
            line.with_user(self.salesperson).write({"discount": 10.0})

    def test_price_edit_allowed_with_context_flag(self):
        """Writing price_unit is allowed with tr_skip_price_protection context."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=5.0)
        line.with_user(self.salesperson).with_context(
            tr_skip_price_protection=True
        ).write({"price_unit": 50.0})
        self.assertAlmostEqual(line.price_unit, 50.0)

    def test_price_edit_allowed_for_system_admin(self):
        """Writing price_unit is allowed for users in group_system."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=5.0)
        admin = self.env.ref("base.user_admin")
        line.with_user(admin).write({"price_unit": 50.0})
        self.assertAlmostEqual(line.price_unit, 50.0)

    def test_non_protected_field_write_allowed(self):
        """Writing non-protected fields does not raise."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=5.0)
        line.with_user(self.salesperson).write({"name": "Updated name"})
        self.assertEqual(line.name, "Updated name")


@tagged("post_install", "-at_install")
class TestComputePriceUnitBranches(CommercialPolicyTestCommon):
    """Cover remaining branches of _compute_price_unit."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_price_unit_zero_when_no_reference_price(self):
        """price_unit is 0 when reference_price is 0 for policy line."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=0.0)
        self.assertAlmostEqual(line.price_unit, 0.0)

    def test_price_unit_calculated_with_discounts(self):
        """price_unit = reference_price * (1 - total_discount/100)."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0, seller_discount=5.0)
        line.write({"extra_discount": 2.0, "extra_discount_reason": "Test"})
        # reference_price ≈ 100 (no contractual return)
        expected = line.reference_price * (1 - 7.0 / 100)
        self.assertAlmostEqual(line.price_unit, expected, places=2)


@tagged("post_install", "-at_install")
class TestRecomputePriceUnitFromPolicy(CommercialPolicyTestCommon):
    """Cover remaining branches of _recompute_price_unit_from_policy."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_recompute_skipped_without_reference_price(self):
        """_recompute_price_unit_from_policy skips line with zero base_price."""
        # Product with no pricelist item → base_price = 0 → reference_price = 0
        product_no_price = (
            self.env["product.template"]
            .create(
                {
                    "name": "No Price Product",
                    "type": "consu",
                    "list_price": 0.0,
                }
            )
            .product_variant_ids[0]
        )
        order = self._create_order()
        line = self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": product_no_price.id,
                "product_uom_qty": 1,
            }
        )
        self.assertAlmostEqual(line.reference_price, 0.0, places=2)
        line._recompute_price_unit_from_policy()
        self.assertAlmostEqual(line.price_unit, 0.0, places=2)

    def test_recompute_updates_price(self):
        """_recompute_price_unit_from_policy updates price from discounts."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0, seller_discount=5.0)
        line.write({"extra_discount": 2.0, "extra_discount_reason": "Test"})
        expected = line.reference_price * (1 - 7.0 / 100)
        self.assertAlmostEqual(line.price_unit, expected, places=2)

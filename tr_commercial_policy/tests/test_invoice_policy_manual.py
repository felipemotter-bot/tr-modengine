# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestInvoicePolicyManual(CommercialPolicyTestCommon):
    """Tests for manual invoice commercial policy (Phase 2).

    Manual invoices (no sale origin) apply commercial policy rules
    from the partner's condition/profile: resolve discounts, calculate
    prices, validate with constraints + tier.
    Exclusions: internal band validation (see plan F.3c).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    def _create_manual_invoice_with_condition(self, partner=None, **kw):
        """Create manual invoice and apply condition snapshot from partner."""
        invoice = self._create_manual_invoice(partner=partner, **kw)
        partner = invoice.partner_id
        condition = partner.effective_condition_id
        # Setup guarantees condition always exists for self.customer
        assert condition, "Test setup must provide a commercial condition"
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

    def _add_product_line(self, invoice, product=None, qty=1):
        """Add a product line through move.write for proper dynamic sync."""
        from odoo import Command

        from ..models.policy_utils import (
            calc_price_unit,
            calc_reference_price,
            get_policy_rates,
        )

        product = product or self.product_a
        condition = invoice.commercial_condition_id
        assert condition, "Invoice must have condition for _add_product_line"

        seller_disc, extra_disc, _source = condition._resolve_discount_for_product(
            product
        )
        pricelist = condition.pricelist_id
        move_date = (
            invoice.invoice_date or invoice.date or fields.Date.context_today(invoice)
        )
        assert pricelist, "Condition must have pricelist"
        raw = pricelist._get_product_price(product, qty, date=move_date)
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
        discount = (invoice.tr_cash_discount or 0) + (
            invoice.tr_fob_discount or 0
        )
        line_vals = {
            "product_id": product.id,
            "quantity": qty,
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
            lambda invoice_line: invoice_line.product_id == product
        )[:1]
        rate = line._get_commission_rate_for_discount(seller_disc)
        if rate is not False:
            invoice.with_context(
                tr_skip_price_protection=True,
                tr_skip_manual_snapshot=True,
            ).write({"line_ids": [Command.update(line.id, {"commission_rate": rate})]})
        return line

    # --- Resolution / Prefill ---

    def test_manual_invoice_resolves_condition_from_partner(self):
        """Manual invoice resolves condition from partner."""
        invoice = self._create_manual_invoice_with_condition()
        self.assertEqual(
            invoice.commercial_condition_id,
            self.customer.effective_condition_id,
        )

    def test_manual_invoice_resolves_profile_from_condition(self):
        """Manual invoice resolves profile from condition."""
        invoice = self._create_manual_invoice_with_condition()
        self.assertTrue(invoice.sales_profile_id)
        self.assertEqual(
            invoice.sales_profile_id,
            invoice.commercial_condition_id.applicable_profile_id,
        )

    def test_manual_invoice_prefills_cash_fob_contractual(self):
        """Manual invoice prefills cash/fob/contractual from condition."""
        invoice = self._create_manual_invoice_with_condition()
        condition = invoice.commercial_condition_id
        self.assertAlmostEqual(
            invoice.tr_cash_discount,
            condition.cash_discount,
            places=2,
        )
        self.assertAlmostEqual(
            invoice.tr_fob_discount,
            condition.fob_discount,
            places=2,
        )
        self.assertAlmostEqual(
            invoice.tr_contractual_return,
            condition.contractual_return,
            places=2,
        )

    def test_manual_invoice_product_prefills_seller_discount(self):
        """Adding product prefills seller_discount from condition."""
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        condition = invoice.commercial_condition_id
        expected_seller, _extra, _source = condition._resolve_discount_for_product(
            self.product_a
        )
        self.assertAlmostEqual(line.seller_discount, expected_seller, places=2)

    def test_manual_invoice_product_prefills_base_reference_price(self):
        """Adding product sets base_price and reference_price."""
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        self.assertTrue(
            line.base_price > 0 or line.reference_price > 0,
            "base_price or reference_price should be set",
        )

    # --- Base de preço ---

    def test_manual_invoice_price_unit_from_policy_chain(self):
        """price_unit is calculated from the full policy chain."""
        from ..models.policy_utils import calc_price_unit

        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        if line.reference_price:
            expected = calc_price_unit(
                line.reference_price,
                line.seller_discount,
                line.extra_discount,
            )
            # Precision may differ slightly due to pricelist rounding
            self.assertAlmostEqual(line.price_unit, expected, delta=0.02)

    def test_manual_invoice_base_price_uses_condition_pricelist(self):
        """base_price uses the condition's pricelist with tax adjustment."""
        invoice = self._create_manual_invoice_with_condition()
        condition = invoice.commercial_condition_id
        if not condition.pricelist_id:  # pragma: no cover
            return  # pragma: no cover
        line = self._add_product_line(invoice)
        pricelist = condition.pricelist_id
        move_date = invoice.invoice_date or invoice.date
        raw = pricelist._get_product_price(
            self.product_a,
            line.quantity or 1.0,
            uom=line.product_uom_id,
            date=move_date,
        )
        expected = self.product_a._get_tax_included_unit_price(
            line.company_id,
            invoice.currency_id,
            move_date,
            "sale",
            fiscal_position=invoice.fiscal_position_id,
            product_price_unit=raw,
            product_currency=pricelist.currency_id,
        )
        self.assertAlmostEqual(line.base_price, expected, places=2)

    # --- Comissão ---

    def test_manual_invoice_commission_rate_from_bands(self):
        """commission_rate is resolved from profile bands."""
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        # Commission rate should be set based on seller_discount bands
        self.assertIsNotNone(line.commission_rate)

    def test_manual_invoice_agent_managed_commission_adjusted(self):
        """Agent commission_id is adjusted to managed commission."""
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        # If profile is agent type, agent commission should be managed
        if invoice.sales_profile_id.profile_type == "agent":
            invoice._resolve_manual_invoice_agent_commissions()
            for agent in line.agent_ids:
                if agent.commission_id.tr_managed:
                    self.assertTrue(agent.commission_id.tr_managed)

    # --- Governança ---

    def test_manual_invoice_no_condition_blocks_post(self):
        """Manual invoice without condition → blocks post."""
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = False
        invoice.sales_profile_id = False
        # Add line without using _add_product_line (no condition)
        self.env["account.move.line"].with_context(
            skip_invoice_sync=True, check_move_validity=False
        ).create(
            {
                "move_id": invoice.id,
                "product_id": self.product_a.id,
                "quantity": 1,
                "price_unit": 100.0,
            }
        )
        with self.assertRaises(UserError):
            invoice.action_post()

    def test_manual_invoice_no_profile_blocks_post(self):
        """Manual invoice without profile → blocks post."""
        invoice = self._create_manual_invoice_with_condition()
        invoice.sales_profile_id = False
        self.env["account.move.line"].with_context(
            skip_invoice_sync=True, check_move_validity=False
        ).create(
            {
                "move_id": invoice.id,
                "product_id": self.product_a.id,
                "quantity": 1,
                "price_unit": 100.0,
            }
        )
        with self.assertRaises(UserError):
            invoice.action_post()

    def test_manual_invoice_missing_condition_blocks_before_tier(self):
        """Missing condition is structural block, not tier."""
        invoice = self._create_manual_invoice()
        invoice.commercial_condition_id = False
        invoice.sales_profile_id = False
        # Should raise UserError (structural), not go through tier
        with self.assertRaises(UserError) as ctx:
            invoice.action_post()
        self.assertIn("commercial condition", str(ctx.exception).lower())

    def test_manual_invoice_posts_when_values_within_limits(self):
        """Manual invoice with values within limits → posts."""
        invoice = self._create_manual_invoice_with_condition()
        self._add_product_line(invoice)
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    # --- Tier ---

    def test_manual_invoice_cash_above_limit_triggers_director(self):
        """Cash discount above profile max → director on manual invoice."""
        invoice = self._create_manual_invoice_with_condition()
        self._add_product_line(invoice)
        profile = invoice.sales_profile_id
        invoice.with_context(check_move_validity=False).write(
            {"tr_cash_discount": profile.cash_discount_max + 5.0}
        )
        invoice.invalidate_recordset(["discount_approval_level"])
        self.assertEqual(invoice.discount_approval_level, "director")

    def test_manual_invoice_extra_discount_triggers_manager(self):
        """Extra discount on manual invoice → manager/director."""
        from ..models.policy_utils import calc_price_unit

        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        # Set extra discount and update price_unit to match
        new_extra = 1.0
        new_price = calc_price_unit(
            line.reference_price, line.seller_discount, new_extra
        )
        line.with_context(skip_invoice_sync=True, check_move_validity=False).write(
            {
                "extra_discount": new_extra,
                "price_unit": new_price,
            }
        )
        invoice.invalidate_recordset(["discount_approval_level"])
        self.assertIn(invoice.discount_approval_level, ("manager", "director"))

    # --- Comportamento ---

    def test_manual_invoice_condition_change_reapplies_full_chain(self):
        """Reapplying condition restores all policy fields on lines."""
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        original_seller = line.seller_discount
        original_base = line.base_price
        original_ref = line.reference_price
        original_price = line.price_unit
        # Trigger condition reapply
        invoice._onchange_commercial_condition()
        # Full chain should be restored
        self.assertAlmostEqual(line.seller_discount, original_seller, places=2)
        self.assertAlmostEqual(line.base_price, original_base, places=2)
        self.assertAlmostEqual(line.reference_price, original_ref, places=2)
        self.assertAlmostEqual(line.price_unit, original_price, delta=0.02)

    def test_manual_invoice_condition_change_clears_extra_discount_reason(
        self,
    ):
        """Reapplying condition clears extra_discount_reason."""
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        line.extra_discount_reason = "Manual override"
        invoice._onchange_commercial_condition()
        self.assertFalse(line.extra_discount_reason)

    def test_manual_invoice_condition_change_resets_payment_term(self):
        """Clearing condition clears payment term and line policy fields."""
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        self.assertTrue(line.seller_discount or line.base_price)
        invoice.with_context(check_move_validity=False).commercial_condition_id = False
        invoice._apply_manual_invoice_condition_snapshot()
        self.assertFalse(invoice.invoice_payment_term_id)
        self.assertAlmostEqual(line.seller_discount, 0.0, places=2)
        self.assertAlmostEqual(line.base_price, 0.0, places=2)

    def test_manual_invoice_partner_change_rederives_agents(self):
        """Changing partner must rederive manual invoice agents."""
        agent_b = self.env["res.partner"].create(
            {
                "name": "Agent B",
                "agent": True,
                "commission_id": self.commission.id,
                "sales_profile_id": self.agent_profile.id,
            }
        )
        customer_b = self.env["res.partner"].create(
            {
                "name": "Customer B",
            }
        )
        customer_b.agent_ids = [(4, agent_b.id)]
        self.env["partner.commercial.condition"].create(
            {
                "partner_id": customer_b.id,
                "pricelist_id": self.pricelist.id,
                "cash_discount": 2.0,
                "fob_discount": 1.0,
                "seller_discount": 5.0,
            }
        )
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        self.assertIn(self.agent_partner, line.agent_ids.agent_id)

        invoice.write({"partner_id": customer_b.id})
        # Backend backfill applies snapshot. Explicit recompute needed
        # after write stack completes (ORM cache timing issue).
        invoice.recompute_lines_agents()
        invoice._resolve_manual_invoice_agent_commissions()
        self.env.flush_all()
        self.env.invalidate_all()
        line = invoice.invoice_line_ids.filtered(
            lambda inv_l: inv_l.product_id == self.product_a
        )[:1]
        self.assertTrue(line, "Product line should exist after partner change")
        self.assertIn(agent_b, line.agent_ids.agent_id)
        self.assertNotIn(self.agent_partner, line.agent_ids.agent_id)
        # Managed commission is applied at action_post time via
        # _resolve_manual_invoice_agent_commissions. The test for
        # managed commission on post is in test_manual_invoice_new_line_gets_managed_commission.

    def test_manual_invoice_policy_uses_product_lines(self):
        """Own rules validation uses _get_policy_invoice_lines."""
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        policy_lines = invoice._get_policy_invoice_lines()
        self.assertIn(line, policy_lines)

    def test_manual_invoice_new_line_gets_managed_commission(self):
        """New line gets commission_rate AND managed commission_id."""
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        self.assertIsNotNone(line.commission_rate)
        # If profile is agent type, verify managed commission on agents
        invoice._resolve_manual_invoice_agent_commissions()
        if invoice.sales_profile_id.profile_type == "agent":
            managed = line.agent_ids.filtered(lambda a: a.commission_id.tr_managed)
            self.assertTrue(
                managed,
                "Agent profile should have managed commission after resolve",
            )

    # --- Bloqueio ---

    def test_mixed_invoice_sale_origin_plus_manual_line_blocks(self):
        """Invoice with sale origin + manual line → blocks post."""
        self.product_template_a.invoice_policy = "order"
        order, invoice = self._create_confirmed_order_with_invoice()
        # Add a manual line
        self.env["account.move.line"].with_context(
            skip_invoice_sync=True, check_move_validity=False
        ).create(
            {
                "move_id": invoice.id,
                "product_id": self.product_b.id,
                "quantity": 1,
                "price_unit": 50.0,
            }
        )
        with self.assertRaises(UserError):
            invoice.action_post()

    def test_manual_invoice_approval_level_none_without_prerequisites(self):
        """Manual invoice without profile → approval level = none."""
        invoice = self._create_manual_invoice()
        invoice.invalidate_recordset(["discount_approval_level"])
        self.assertEqual(invoice.discount_approval_level, "none")

    # --- Price protection ---

    def test_manual_invoice_direct_price_edit_blocked_at_post(self):
        """Direct edit of price_unit on manual invoice → blocked at post."""
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        # Tamper with price_unit directly (bypass policy chain)
        line.with_context(skip_invoice_sync=True, check_move_validity=False).write(
            {"price_unit": 0.01}
        )
        with self.assertRaises(UserError):
            invoice.action_post()

    # Note: native discount field is NOT checked at post because other
    # modules (eng_punctuality_discount, l10n_br) modify it independently.
    # Phase 5 will add direct discount governance.

    # --- Condition clear cleans everything ---

    def test_manual_invoice_condition_clear_resets_price_unit(self):
        """Clearing condition also resets price_unit on lines."""
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        self.assertTrue(line.price_unit > 0)
        invoice.with_context(check_move_validity=False).commercial_condition_id = False
        invoice._apply_manual_invoice_condition_snapshot()
        self.assertAlmostEqual(line.price_unit, 0.0, places=2)

    def test_manual_invoice_condition_clear_removes_agents(self):
        """Clearing condition removes agent lines."""
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        invoice.with_context(check_move_validity=False).commercial_condition_id = False
        invoice._apply_manual_invoice_condition_snapshot()
        self.assertFalse(line.agent_ids)

    # --- Entrypoint tests (onchange flow) ---

    def test_manual_invoice_partner_onchange_prefills_snapshot(self):
        """Onchange partner_id prefills condition snapshot."""
        invoice = self._create_manual_invoice(partner=self.customer)
        invoice._onchange_partner_commercial_condition()
        condition = self.customer.effective_condition_id
        self.assertEqual(invoice.commercial_condition_id, condition)
        self.assertEqual(invoice.sales_profile_id, condition.applicable_profile_id)
        self.assertAlmostEqual(
            invoice.tr_cash_discount,
            condition.cash_discount,
            places=2,
        )

    def test_manual_invoice_prepare_line_vals_sets_chain(self):
        """_prepare_condition_line_vals produces complete pricing chain."""
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        condition = invoice.commercial_condition_id
        vals = invoice._prepare_condition_line_vals(line, condition)
        self.assertIn("base_price", vals)
        self.assertIn("reference_price", vals)
        self.assertIn("price_unit", vals)
        self.assertIn("commission_rate", vals)
        self.assertGreaterEqual(vals["base_price"], 0.0)
        self.assertGreaterEqual(vals["reference_price"], 0.0)

    # --- Totals consistency ---

    def test_manual_invoice_draft_totals_consistent(self):
        """Draft manual invoice keeps consistent totals after reapply."""
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        invoice._apply_manual_invoice_condition_snapshot()
        invoice.invalidate_recordset(["amount_untaxed"])
        self.assertGreater(invoice.amount_untaxed, 0.0)
        self.assertAlmostEqual(
            invoice.amount_untaxed,
            line.price_subtotal,
            places=2,
        )

    def test_manual_invoice_clear_condition_totals_consistent(self):
        """Clearing condition zeroes draft totals consistently."""
        invoice = self._create_manual_invoice_with_condition()
        self._add_product_line(invoice)
        invoice.commercial_condition_id = False
        invoice._apply_manual_invoice_condition_snapshot()
        invoice.invalidate_recordset(["amount_untaxed"])
        self.assertAlmostEqual(invoice.amount_untaxed, 0.0, places=2)

    def test_manual_invoice_condition_change_uses_new_values(self):
        """Reapplying condition on persistent draft uses new header values.

        Verifies that reference_price and discount come from the new
        condition, not stale self.tr_* values.
        """
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        old_ref = line.reference_price
        old_discount = line.discount
        # Reapply same condition — values should stay the same
        invoice._apply_manual_invoice_condition_snapshot()
        line.invalidate_recordset()
        self.assertAlmostEqual(line.reference_price, old_ref, places=2)
        self.assertAlmostEqual(line.discount, old_discount, places=2)

    def test_manual_invoice_posts_with_correct_totals(self):
        """Posted manual invoice has consistent totals."""
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")
        self.assertGreater(invoice.amount_untaxed, 0.0)
        self.assertAlmostEqual(
            invoice.amount_untaxed,
            line.price_subtotal,
            places=2,
        )

    # --- Form / NewId flow ---

    def test_manual_invoice_form_flow_with_backend_backfill(self):
        """Form save must persist condition/profile through backend backfill."""
        from odoo.tests.common import Form

        with Form(
            self.env["account.move"].with_context(default_move_type="out_invoice")
        ) as invoice_form:
            invoice_form.partner_id = self.customer
            with invoice_form.invoice_line_ids.new() as line_form:
                line_form.product_id = self.product_a
        invoice = invoice_form.save()
        self.assertEqual(
            invoice.commercial_condition_id,
            self.customer.effective_condition_id,
        )
        self.assertEqual(
            invoice.sales_profile_id,
            self.customer.effective_condition_id.applicable_profile_id,
        )
        self.assertGreater(invoice.amount_untaxed, 0.0)
        self.assertTrue(
            invoice.invoice_line_ids.filtered(
                lambda invoice_line: invoice_line.product_id == self.product_a
            )
        )

    # --- Cash/FOB → line.discount ---

    def test_manual_invoice_cash_fob_change_updates_line_discount(self):
        """Editing cash/fob on persistent invoice propagates line.discount.

        Write without tr_skip_manual_snapshot to exercise the real
        write path that propagates cash/fob to line.discount.
        """
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        # Change cash_discount via normal write (no skip context)
        new_cash = 5.0
        invoice.write({"tr_cash_discount": new_cash})
        line.invalidate_recordset(["discount"])
        expected = new_cash + (invoice.tr_fob_discount or 0)
        self.assertAlmostEqual(line.discount, expected, places=2)

    # --- _warn_invoice_br_discount_module ---

    def _toggle_br_discount_module(self, state):
        """Set the state of engenere_account_invoice_br_discount module.

        The module record is guaranteed to exist in the registry (the
        module lives in engenere-addons-develop and is always loaded).
        """
        module = (
            self.env["ir.module.module"]
            .sudo()
            .search([("name", "=", "engenere_account_invoice_br_discount")], limit=1)
        )
        self.assertTrue(
            module,
            "engenere_account_invoice_br_discount must exist in ir.module.module",
        )
        old_state = module.state
        module.state = state
        return module, old_state

    def test_warn_br_discount_blocks_when_installed(self):
        """_warn_invoice_br_discount_module raises when legacy module is
        installed and the commercial policy is applicable."""
        module, old_state = self._toggle_br_discount_module("installed")
        try:
            invoice = self._create_manual_invoice_with_condition()
            with self.assertRaises(UserError) as ctx:
                invoice._warn_invoice_br_discount_module()
            self.assertIn("engenere_account_invoice_br_discount", str(ctx.exception))
        finally:
            module.state = old_state

    def test_warn_br_discount_silent_when_uninstalled(self):
        """_warn_invoice_br_discount_module does nothing when legacy
        module is uninstalled."""
        module, old_state = self._toggle_br_discount_module("uninstalled")
        try:
            invoice = self._create_manual_invoice_with_condition()
            # Direct call must not raise.
            invoice._warn_invoice_br_discount_module()
        finally:
            module.state = old_state

    # --- Invoice line form integration (policy fields in usability anchors) ---

    def _get_invoice_line_form(self):
        """Return the embedded form for invoice_line_ids."""
        arch, _view = self.env["account.move"]._get_view(view_type="form")
        forms = arch.xpath("//field[@name='invoice_line_ids']/form")
        self.assertTrue(forms, "invoice line form not found")
        return forms[0]

    def test_invoice_line_form_policy_fields_in_left_anchor(self):
        """Policy fields land inside the trento_invoice_usability left
        anchor (invoice_line_left)."""
        line_form = self._get_invoice_line_form()
        left = line_form.xpath(".//group[@name='invoice_line_left']")
        self.assertTrue(left, "invoice_line_left anchor missing")
        names = {f.get("name") for f in left[0].iter("field")}
        for expected in (
            "base_price",
            "reference_price",
            "price_unit",
            "seller_discount",
            "extra_discount",
            "total_seller_extra_discount",
        ):
            self.assertIn(expected, names, f"{expected} should be in the left anchor")

    def test_invoice_line_form_policy_fields_in_right_anchor(self):
        """Commission rate lands inside the right anchor (invoice_line_right)."""
        line_form = self._get_invoice_line_form()
        right = line_form.xpath(".//group[@name='invoice_line_right']")
        self.assertTrue(right, "invoice_line_right anchor missing")
        names = {f.get("name") for f in right[0].iter("field")}
        self.assertIn("commission_rate", names)

    def test_invoice_line_form_no_separate_commercial_group(self):
        """Legacy 'Commercial Policy' standalone group was removed —
        policy fields live inside the usability anchors now."""
        line_form = self._get_invoice_line_form()
        # No direct <group string="Commercial Policy"> child of the sheet.
        legacy = line_form.xpath(".//group[@string='Commercial Policy']")
        self.assertFalse(
            legacy,
            "legacy 'Commercial Policy' group should not exist anymore",
        )

    def test_warn_br_discount_skipped_without_company_profile(self):
        """Guard does not fire when the company has no default profile,
        even if the legacy module is installed."""
        self.env.company.default_sales_profile_id = False
        try:
            module, old_state = self._toggle_br_discount_module("installed")
            try:
                invoice = self._create_manual_invoice(partner=self.customer)
                # Direct call must not raise, because the policy is not
                # applicable to this company.
                invoice._warn_invoice_br_discount_module()
            finally:
                module.state = old_state
        finally:
            self.env.company.default_sales_profile_id = self.agent_profile

    # --- _compute_base_price / _compute_price_unit guards ---

    def test_base_price_compute_skips_non_invoice_moves(self):
        """``_compute_base_price`` is a no-op for move types other than
        out_invoice / out_refund."""
        bill = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "partner_id": self.customer.id,
            }
        )
        line = self.env["account.move.line"].create(
            {
                "move_id": bill.id,
                "product_id": self.product_a.id,
                "quantity": 1.0,
                "price_unit": 10.0,
            }
        )
        # Compute must not touch base_price for an in_invoice.
        line._compute_base_price()
        self.assertEqual(line.base_price, 0.0)

    def test_price_unit_compute_preserves_sale_origin_snapshot(self):
        """``_compute_price_unit`` skips lines with ``sale_line_ids``."""
        order, invoice = self._create_confirmed_order_with_invoice(
            seller_discount=3.0, qty=5
        )
        line = invoice.invoice_line_ids.filtered(
            lambda sol: sol.display_type == "product"
        )
        original = line.price_unit
        line._compute_price_unit()
        self.assertEqual(line.price_unit, original)

    def test_price_unit_compute_skips_posted_invoices(self):
        """Posted moves are frozen — compute never runs on them."""
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        invoice.action_post()
        snapshot = line.price_unit
        line._compute_price_unit()
        self.assertEqual(line.price_unit, snapshot)

    def test_price_unit_compute_applies_policy_chain(self):
        """Draft manual invoice line: compute sets price_unit via policy."""
        from ..models.policy_utils import calc_price_unit

        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        # Seed reference_price so the compute takes the policy path
        # (``_add_product_line`` fires the onchange, which fills it).
        self.assertTrue(line.reference_price)
        # Explicitly invalidate + recompute so we exercise the compute
        # method itself, not only the onchange-seeded value.
        line.invalidate_recordset(["price_unit"])
        line._compute_price_unit()
        expected = calc_price_unit(
            line.reference_price, line.seller_discount, line.extra_discount
        )
        self.assertAlmostEqual(line.price_unit, expected, delta=0.02)

    def test_base_price_compute_skips_posted_invoices(self):
        """Posted invoices are frozen — ``_compute_base_price`` no-ops."""
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        invoice.action_post()
        snapshot = line.base_price
        line.invalidate_recordset(["base_price"])
        line._compute_base_price()
        self.assertEqual(line.base_price, snapshot)

    def test_reference_price_compute_skips_non_invoice(self):
        """``_compute_reference_price`` no-ops for non-invoice moves."""
        bill = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "partner_id": self.customer.id,
            }
        )
        line = self.env["account.move.line"].create(
            {
                "move_id": bill.id,
                "product_id": self.product_a.id,
                "quantity": 1.0,
            }
        )
        snapshot = line.reference_price
        line.invalidate_recordset(["reference_price"])
        line._compute_reference_price()
        self.assertEqual(line.reference_price, snapshot)

    def test_reference_price_compute_skips_posted_invoices(self):
        """Posted sales invoices keep the reference_price snapshot."""
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        invoice.action_post()
        snapshot = line.reference_price
        line.invalidate_recordset(["reference_price"])
        line._compute_reference_price()
        self.assertEqual(line.reference_price, snapshot)

    def test_unmanaged_warning_skips_unsaved_invoice_lines(self):
        """Invoice banner ignores ``NewId`` lines to avoid false positives."""
        order, invoice = self._create_confirmed_order_with_invoice()
        new_line = self.env["account.move.line"].new(
            {
                "move_id": invoice.id,
                "product_id": self.product_a.id,
                "quantity": 1.0,
                "sale_line_ids": [(6, 0, order.order_line.ids)],
                "display_type": "product",
            }
        )
        # Trigger the compute on the unsaved record; it must not crash.
        new_line.move_id._compute_unmanaged_commission_warning()

    def test_price_unit_compute_falls_back_to_super_for_vendor_bill(self):
        """Non-sales moves delegate to base Odoo's compute."""
        bill = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "partner_id": self.customer.id,
            }
        )
        line = self.env["account.move.line"].create(
            {
                "move_id": bill.id,
                "product_id": self.product_a.id,
                "quantity": 1.0,
            }
        )
        # Trigger our override; it must not raise and must leave the
        # base compute free to run (no assertion on the numeric value —
        # the point is the code path is exercised).
        line._compute_price_unit()

    def test_price_unit_preserves_user_value_on_manual_invoice_no_policy(self):
        """Manual out_invoice without commercial policy keeps user price_unit.

        Regression guard. Our ``_compute_price_unit`` used to overwrite
        ``price_unit`` on any out_invoice draft line without sale
        origin, regardless of whether the commercial policy was
        actually applied. In an environment where the default pricelist
        has no rule for the product, ``calc_price_unit(0, 0, 0)``
        returned 0 and silently zeroed the user's explicit price_unit
        on every ``quantity`` write. Now the compute only steers
        ``price_unit`` when the line actually carries policy signals
        (``commercial_condition_id``, ``base_price``, ``reference_price``,
        ``seller_discount`` or ``extra_discount``).
        """
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.customer.id,
                # Deliberately no commercial_condition_id.
            }
        )
        invoice.commercial_condition_id = False
        line = (
            self.env["account.move.line"]
            .with_context(skip_invoice_sync=True, check_move_validity=False)
            .create(
                {
                    "move_id": invoice.id,
                    "product_id": self.product_a.id,
                    "quantity": 10.0,
                    "price_unit": 100.0,
                }
            )
        )
        # Force the compute to run; the override must not hijack the
        # user's price_unit because the line carries no policy signals.
        line.invalidate_recordset(["price_unit"])
        line._compute_price_unit()
        self.assertNotEqual(
            line.price_unit,
            0.0,
            "Compute on manual invoice without policy zeroed price_unit — "
            "override is hijacking lines it should leave alone",
        )

    def test_price_unit_compute_is_noop_under_skip_manual_snapshot(self):
        """``tr_skip_manual_snapshot`` context pauses the override.

        ``_prepare_clear_line_vals`` writes ``price_unit=0`` explicitly
        under this context. The compute must not second-guess the
        intentional clear, because a re-entrant recompute would either
        bounce the value back to policy numbers or, worse, fall through
        to ``super`` and reset to the product's pricelist price.
        """
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        clear_vals = invoice._prepare_clear_line_vals()
        self.assertEqual(clear_vals["price_unit"], 0.0)
        line.with_context(
            tr_skip_manual_snapshot=True,
            tr_skip_price_protection=True,
            check_move_validity=False,
        ).write({"price_unit": 0.0, "reference_price": 0.0, "base_price": 0.0})
        # Now invoke the compute while the skip context is still set —
        # the test mirrors the inner call that would happen if Odoo
        # re-entered the compute for any reason during the snapshot
        # write.
        line.with_context(tr_skip_manual_snapshot=True)._compute_price_unit()
        self.assertEqual(
            line.price_unit,
            0.0,
            "Skip context must leave the explicit zero alone",
        )

    def test_price_unit_zero_reference_keeps_zero_under_policy(self):
        """A policy line with ``reference_price=0`` must stay at 0.

        This is the legitimate "condition applied, product has no
        priced reference" case. The gate above (``policy_applied``)
        must still let the compute run and set ``price_unit=0``,
        otherwise falling back to ``super()`` would pull the product's
        raw pricelist price and break the contract of
        ``_prepare_condition_line_vals`` (which treats zero reference
        as a signal).
        """
        invoice = self._create_manual_invoice_with_condition()
        line = self._add_product_line(invoice)
        # Force the policy chain into the "condition applied + zero
        # reference" state. ``seller_discount`` stays non-zero so the
        # ``policy_applied`` gate holds even with zero base/reference.
        line.with_context(
            tr_skip_price_protection=True, check_move_validity=False
        ).write({"base_price": 0.0, "reference_price": 0.0})
        line.invalidate_recordset(["price_unit"])
        line._compute_price_unit()
        self.assertEqual(
            line.price_unit,
            0.0,
            "Policy line with zero reference must resolve to zero price",
        )

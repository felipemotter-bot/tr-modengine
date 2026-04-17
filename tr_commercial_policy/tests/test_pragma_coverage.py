# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Tests for invoice policy edge cases and integration paths.

Covers business branches, guard conditions, tier integration,
onchange/NewId paths, and commission fallback scenarios.
"""

from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form, tagged

from ..models.policy_utils import get_extra_discount_approval_level
from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestInvoicePolicyBusinessBranches(CommercialPolicyTestCommon):
    """Category E — Business branches that were never exercised."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    # --- policy_utils: zero discount → "none" ---

    def test_extra_discount_approval_zero_returns_none(self):
        """get_extra_discount_approval_level(0, ...) → 'none'."""
        self.assertEqual(get_extra_discount_approval_level(0, 5.0), "none")

    def test_extra_discount_approval_negative_returns_none(self):
        """get_extra_discount_approval_level(-1, ...) → 'none'."""
        self.assertEqual(get_extra_discount_approval_level(-1, 5.0), "none")

    def test_extra_discount_approval_false_returns_none(self):
        """get_extra_discount_approval_level(False, ...) → 'none'."""
        self.assertEqual(get_extra_discount_approval_level(False, 5.0), "none")

    # --- account_move: no partner → UserError ---

    def test_manual_invoice_no_partner_blocks(self):
        """Manual out_invoice without partner → UserError on action_post."""
        invoice = self.env["account.move"].create({"move_type": "out_invoice"})
        with self.assertRaises(UserError):
            invoice.action_post()

    # --- account_move: _get_effective_pricelist with sale_origin ---

    def test_effective_pricelist_sale_origin(self):
        """Sale-origin invoice returns pricelist from the sale order."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        pricelist = invoice._get_effective_pricelist()
        self.assertEqual(pricelist, _order.pricelist_id)

    # --- account_move: extra_discount_director issue ---

    def test_extra_discount_director_issue(self):
        """Extra discount above manager_limit → director-level issue."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        profile = invoice.sales_profile_id
        profile.manager_extra_limit = 5.0
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        # Set extra_discount above manager_limit → "director"
        inv_line.with_context(skip_invoice_sync=True, check_move_validity=False).write(
            {"extra_discount": 10.0}
        )
        issues = invoice._get_invoice_discount_validation_issues()
        director_issues = [i for i in issues if i["level"] == "director"]
        self.assertTrue(director_issues)
        self.assertEqual(director_issues[0]["type"], "extra_discount_director")

    # --- account_move_line: extra_discount > 99 constraint ---

    def test_extra_discount_above_99_raises(self):
        """Extra discount > 99% on invoice line → ValidationError."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        with self.assertRaises(ValidationError):
            inv_line.with_context(
                tr_skip_price_protection=True, check_move_validity=False
            ).write({"extra_discount": 100.0})

    # --- account_move_line: _get_applicable_rule branches ---

    def test_applicable_rule_variant(self):
        """Rule applied_on='product' matches specific variant."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        profile = invoice.sales_profile_id
        # Create a variant-specific rule
        variant_rule = self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": profile.id,
                "applied_on": "product",
                "product_id": self.product_a.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 10.0, "commission_rate": 15.0}),
                ],
            }
        )
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        result = inv_line._get_applicable_rule()
        self.assertEqual(result, variant_rule)

    def test_applicable_rule_template(self):
        """Rule applied_on='product_template' matches template."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        profile = invoice.sales_profile_id
        tmpl_rule = self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": profile.id,
                "applied_on": "product_template",
                "product_tmpl_id": self.product_a.product_tmpl_id.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 10.0, "commission_rate": 12.0}),
                ],
            }
        )
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        result = inv_line._get_applicable_rule()
        self.assertEqual(result, tmpl_rule)

    def test_applicable_rule_category(self):
        """Rule applied_on='category' matches product category."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        profile = invoice.sales_profile_id
        categ_rule = self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": profile.id,
                "applied_on": "category",
                "categ_id": self.categ_chemicals.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 10.0, "commission_rate": 8.0}),
                ],
            }
        )
        # Remove general rule so category is the best match
        self.general_rule.unlink()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        result = inv_line._get_applicable_rule()
        self.assertEqual(result, categ_rule)
        # Recreate general rule for other tests
        self.general_rule = self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": profile.id,
                "applied_on": "general",
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 5.0, "commission_rate": 10.0}),
                    (0, 0, {"discount_up_to": 10.0, "commission_rate": 7.0}),
                ],
            }
        )

    def test_applicable_rule_no_match_returns_empty(self):
        """No matching rule → empty recordset."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        # Remove all rules
        profile = invoice.sales_profile_id
        profile.rule_ids.unlink()
        result = inv_line._get_applicable_rule()
        self.assertFalse(result)
        # Recreate general rule
        self.general_rule = self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": profile.id,
                "applied_on": "general",
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 5.0, "commission_rate": 10.0}),
                    (0, 0, {"discount_up_to": 10.0, "commission_rate": 7.0}),
                ],
            }
        )

    # --- account_move_line: order_value_band_ids max discount ---

    def test_seller_discount_max_from_order_value_bands(self):
        """_get_seller_discount_absolute_max uses band max when bands exist."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        # Add order value bands to the general rule
        rule = inv_line._get_applicable_rule()
        rule.write(
            {
                "order_value_band_ids": [
                    (
                        0,
                        0,
                        {
                            "order_min_amount": 0,
                            "seller_discount_max": 8.0,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "order_min_amount": 1000,
                            "seller_discount_max": 15.0,
                        },
                    ),
                ],
            }
        )
        max_disc = inv_line._get_seller_discount_absolute_max()
        self.assertAlmostEqual(max_disc, 15.0)

    # --- _is_commercial_policy_applicable ---

    def test_policy_not_applicable_without_profile(self):
        """Invoice without any profile/condition skips policy check."""
        # Create partner without condition
        partner = self.env["res.partner"].create({"name": "No Policy Partner"})
        invoice = self.env["account.move"].create(
            {"move_type": "out_invoice", "partner_id": partner.id}
        )
        # Clear default on the invoice's company (not just env.company)
        invoice.company_id.default_sales_profile_id = False
        self.assertFalse(invoice._is_commercial_policy_applicable())

    def test_policy_applicable_with_company_default(self):
        """Invoice is governed when company has default profile."""
        partner = self.env["res.partner"].create({"name": "No Policy Partner"})
        invoice = self.env["account.move"].create(
            {"move_type": "out_invoice", "partner_id": partner.id}
        )
        # Setup guarantees default_sales_profile_id is set
        self.assertTrue(invoice._is_commercial_policy_applicable())

    def test_policy_applicable_with_sale_origin(self):
        """Sale-origin invoice is governed even without explicit profile."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        # Clear profile so the has_sale_origin branch is the one that returns True
        invoice.sales_profile_id = False
        self.assertTrue(invoice.has_sale_origin)
        self.assertTrue(invoice._is_commercial_policy_applicable())

    def test_plain_invoice_posts_without_policy(self):
        """Invoice without profile/condition posts freely (no UserError)."""
        partner = self.env["res.partner"].create({"name": "Plain Partner"})
        invoice = self.env["account.move"].create(
            {"move_type": "out_invoice", "partner_id": partner.id}
        )
        invoice.company_id.default_sales_profile_id = False
        invoice.with_context(
            tr_skip_price_protection=True, check_move_validity=False
        ).write(
            {
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "quantity": 1,
                            "price_unit": 100.0,
                        },
                    )
                ]
            }
        )
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    # --- Display computes (moved from trento_invoice_usability) ---

    def test_invoice_discount_pct(self):
        """invoice_discount_pct = cash + fob."""
        invoice = self._create_manual_invoice()
        invoice.write({"tr_cash_discount": 3.0, "tr_fob_discount": 1.5})
        invoice.invalidate_recordset(["invoice_discount_pct"])
        self.assertAlmostEqual(invoice.invoice_discount_pct, 4.5)

    def test_adjustment_factor_display_with_return(self):
        """adjustment_factor_display shows factor when contractual return > 0."""
        invoice = self._create_manual_invoice()
        invoice.tr_contractual_return = 12.7

        self.assertIn("+", invoice.adjustment_factor_display)

    def test_adjustment_factor_display_without_return(self):
        """adjustment_factor_display is empty when no contractual return."""
        invoice = self._create_manual_invoice()
        self.assertEqual(invoice.adjustment_factor_display, "")

    def test_effective_pricelist_non_invoice(self):
        """effective_pricelist_id is False for non-invoice types."""
        move = self.env["account.move"].create(
            {"move_type": "entry", "partner_id": self.customer.id}
        )
        self.assertFalse(move.effective_pricelist_id)

    def test_total_seller_extra_discount(self):
        """total_seller_extra_discount = seller + extra."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        inv_line.with_context(
            tr_skip_price_protection=True, check_move_validity=False
        ).write({"seller_discount": 5.0, "extra_discount": 2.0})
        self.assertAlmostEqual(inv_line.total_seller_extra_discount, 7.0)

    def test_seller_discount_max_non_invoice(self):
        """seller_discount_max is 0 for non-sale move types."""
        move = self.env["account.move"].create(
            {"move_type": "in_invoice", "partner_id": self.customer.id}
        )
        move.with_context(
            tr_skip_price_protection=True, check_move_validity=False
        ).write(
            {
                "line_ids": [
                    (0, 0, {"product_id": self.product_a.id, "price_unit": 100.0})
                ]
            }
        )
        line = move.invoice_line_ids.filtered(lambda line: line.product_id)[:1]
        self.assertEqual(line.seller_discount_max, 0.0)


@tagged("post_install", "-at_install")
class TestInvoicePolicyGuards(CommercialPolicyTestCommon):
    """Category B — Guard branches (early returns for missing data)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    # --- account_move: onchange partner with sale_origin (line 120) ---

    def test_sale_origin_invoice_ignores_partner_condition_onchange(self):
        """_onchange_partner_commercial_condition returns early for sale origin."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        original_condition = invoice.commercial_condition_id
        # Trigger onchange — should skip because has_sale_origin
        invoice._onchange_partner_commercial_condition()
        self.assertEqual(invoice.commercial_condition_id, original_condition)

    # --- account_move: onchange condition with sale_origin (line 163) ---

    def test_sale_origin_invoice_ignores_condition_snapshot_onchange(self):
        """_apply_manual_invoice_condition_snapshot returns early for sale."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        # Should return early without error
        invoice._apply_manual_invoice_condition_snapshot()

    # --- account_move: _prepare_condition_line_vals no condition (line 232) ---

    def test_condition_line_vals_empty_without_condition(self):
        """_prepare_condition_line_vals with no condition → empty dict."""
        invoice = self._create_manual_invoice()
        line = invoice.invoice_line_ids[:1] or invoice
        result = invoice._prepare_condition_line_vals(line, False)
        self.assertEqual(result, {})

    # --- account_move: _check_mixed_invoice_lines no sale origin (line 362) ---

    def test_manual_invoice_has_no_mixed_line_violation(self):
        """_check_mixed_invoice_lines returns early when not sale origin."""
        invoice = self._create_manual_invoice()
        # Should not raise
        invoice._check_mixed_invoice_lines()

    # --- account_move: _is_within_validity no orders (line 545) ---

    def test_manual_invoice_always_within_validity(self):
        """_is_within_validity returns True when no origin orders."""
        invoice = self._create_manual_invoice()
        invoice.company_id.invoice_validity_days = 30
        self.assertTrue(invoice._is_within_validity())

    # --- account_move: snapshot no sale_line (line 683) ---

    def test_snapshot_check_ignores_manual_lines(self):
        """_get_invoice_snapshot_issues skips lines without sale_line_ids."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        # Add a manual line to the sale-origin invoice
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
                            "product_id": self.product_b.id,
                            "quantity": 1,
                            "price_unit": 50.0,
                        },
                    )
                ]
            }
        )
        # Should not crash — manual line is skipped
        issues = invoice._get_invoice_snapshot_issues()
        self.assertIsInstance(issues, list)

    # --- account_move: extra_discount snapshot divergence (line 709) ---

    def test_extra_discount_divergence_triggers_own_rule(self):
        """Divergent extra_discount triggers own_rule issue."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        inv_line.with_context(skip_invoice_sync=True, check_move_validity=False).write(
            {"extra_discount": 7.0}
        )
        issues = invoice._get_invoice_snapshot_issues()
        extra_issues = [i for i in issues if i["kind"] == "extra_discount"]
        self.assertTrue(extra_issues)

    # --- account_move: payment_term else branch (line 906) ---

    def test_payment_term_avg_days_with_percent_line(self):
        """Payment term with non-balance line uses value_amount as weight."""
        # Create a payment term with a percent line + balance
        term = self.env["account.payment.term"].create(
            {
                "name": "Mixed Term",
                "line_ids": [
                    (0, 0, {"value": "percent", "value_amount": 50.0, "days": 30}),
                    (0, 0, {"value": "balance", "days": 60}),
                ],
            }
        )
        invoice = self._create_manual_invoice()
        invoice.invoice_payment_term_id = term
        invoice.invalidate_recordset(["payment_term_avg_days"])
        # Should compute without error — exercises the else branch
        avg = invoice.payment_term_avg_days
        self.assertGreater(avg, 0)

    # --- account_move: _get_invoice_discount_validation_issues guards (973-976) ---

    def test_discount_validation_empty_without_profile(self):
        """_get_invoice_discount_validation_issues with no profile → []."""
        invoice = self._create_manual_invoice()
        # No profile set
        invoice.sales_profile_id = False
        issues = invoice._get_invoice_discount_validation_issues()
        self.assertEqual(issues, [])

    # --- account_move: _build_approval_snapshot no issues (line 1084) ---

    def test_approval_snapshot_empty_when_no_issues(self):
        """_build_approval_snapshot([]) → False."""
        invoice = self._create_manual_invoice()
        self.assertFalse(invoice._build_approval_snapshot([]))

    # --- account_move: resync no sale_line (line 1334) ---

    def test_resync_ignores_manual_lines(self):
        """action_resync_from_sale_order skips lines without sale_line_ids."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        # Add manual line
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
                            "product_id": self.product_b.id,
                            "quantity": 1,
                            "price_unit": 50.0,
                        },
                    )
                ]
            }
        )
        # Should not crash
        invoice.action_resync_from_sale_order()

    # --- account_move_line: _compute_manual_base_price no pricelist (line 55) ---

    def test_base_price_zero_without_pricelist(self):
        """_compute_manual_base_price without pricelist → 0.0."""
        invoice = self._create_manual_invoice()
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
                            "product_id": self.product_a.id,
                            "quantity": 1,
                            "price_unit": 100.0,
                        },
                    )
                ]
            }
        )
        line = invoice.invoice_line_ids.filtered(
            lambda inv_line: inv_line.display_type == "product"
        )[:1]
        # No condition → no pricelist
        invoice.commercial_condition_id = False
        result = line._compute_manual_base_price()
        self.assertEqual(result, 0.0)

    # --- account_move_line: _get_commission_rate guards (248-257) ---

    def test_commission_rate_false_without_matching_rule(self):
        """_get_commission_rate_for_discount with no rule → False."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        profile = invoice.sales_profile_id
        # Remove all rules
        profile.rule_ids.unlink()
        result = inv_line._get_commission_rate_for_discount(0.0)
        self.assertFalse(result)
        # Recreate general rule
        self.general_rule = self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": profile.id,
                "applied_on": "general",
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 5.0, "commission_rate": 10.0}),
                    (0, 0, {"discount_up_to": 10.0, "commission_rate": 7.0}),
                ],
            }
        )

    def test_commission_rate_false_when_discount_exceeds_bands(self):
        """Discount above all bands → False."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        # All bands have discount_up_to <= 10
        result = inv_line._get_commission_rate_for_discount(999.0)
        self.assertFalse(result)


@tagged("post_install", "-at_install")
class TestInvoicePolicyTierIntegration(CommercialPolicyTestCommon):
    """Category D — Tier integration and notifications."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    # --- D-core: need_validation raise (line 399) ---

    def test_manual_invoice_need_validation_blocks(self):
        """Manual invoice with need_validation → UserError on action_post."""
        invoice = self._create_manual_invoice()
        invoice.write(
            {
                "commercial_condition_id": self.condition.id,
                "sales_profile_id": self.condition.applicable_profile_id.id,
                "tr_cash_discount": self.condition.cash_discount,
                "tr_fob_discount": self.condition.fob_discount,
                "tr_contractual_return": self.condition.contractual_return,
            }
        )
        invoice.with_context(
            tr_skip_price_protection=True,
            check_move_validity=False,
            tr_skip_manual_snapshot=True,
        ).write(
            {
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "quantity": 1,
                            "price_unit": 100.0,
                            "reference_price": 100.0,
                            "base_price": 100.0,
                        },
                    )
                ]
            }
        )
        # Mock need_validation to return True — avoids dependency on tier definitions
        with patch.object(
            type(invoice),
            "need_validation",
            new_callable=lambda: property(lambda s: True),
        ):
            with self.assertRaises(UserError):
                invoice.action_post()

    # --- D-core: _get_under_validation_exceptions (lines 1215-1224) ---

    def test_under_validation_exceptions_includes_policy_fields(self):
        """_get_under_validation_exceptions includes policy fields."""
        AccountMove = self.env["account.move"]
        exceptions = AccountMove._get_under_validation_exceptions()
        self.assertIn("tr_cash_discount", exceptions)
        self.assertIn("tr_fob_discount", exceptions)
        self.assertIn("invoice_payment_term_id", exceptions)
        self.assertIn("invoice_line_ids", exceptions)

    # --- D-core: _sync_discount_validation_state (lines 1269, 1274-1276) ---

    def test_sync_validation_on_discount_field_change(self):
        """Writing discount fields on move with review_ids triggers sync."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        # Mock review_ids and restart_validation
        with patch.object(
            type(invoice),
            "review_ids",
            new_callable=lambda: property(lambda s: s.env["tier.review"]),
        ):
            with patch.object(type(invoice), "restart_validation") as mock_restart:
                invoice._sync_discount_validation_state()
                # review_ids is empty mock, so restart not called
                mock_restart.assert_not_called()

    # --- D-acessória: _notify_accepted_reviews_body (lines 1280-1285) ---

    def test_move_write_discount_restarts_validation(self):
        """Writing tr_cash_discount on move with review_ids syncs."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        # Create a tier review so review_ids is truthy
        self.env["tier.review"].create(
            {
                "model": "account.move",
                "res_id": invoice.id,
                "reviewer_id": self.env.user.id,
                "status": "pending",
                "sequence": 10,
            }
        )
        with patch.object(type(invoice), "restart_validation") as mock_restart:
            invoice.with_context(check_move_validity=False).write(
                {"tr_cash_discount": 1.0}
            )
            mock_restart.assert_called()

    # --- _get_invoice_discount_validation_issues UserError guard (973-974) ---

    def test_discount_issues_empty_when_profile_error(self):
        """UserError from _get_effective_sales_profile → returns []."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        with patch.object(
            type(invoice),
            "_get_effective_sales_profile",
            side_effect=UserError("broken"),
        ):
            issues = invoice._get_invoice_discount_validation_issues()
        self.assertEqual(issues, [])

    def test_approval_notification_without_snapshot(self):
        """_notify_accepted_reviews_body without snapshot → base body."""
        invoice = self._create_manual_invoice()
        invoice.discount_approval_snapshot = False
        body = invoice._notify_accepted_reviews_body()
        # Should return base body without violations
        self.assertNotIn("policy exceptions", body or "")

    def test_approval_notification_includes_violations(self):
        """_notify_accepted_reviews_body with snapshot → includes violations."""
        invoice = self._create_manual_invoice()
        # Force a snapshot value
        invoice.with_context(check_move_validity=False).write(
            {"discount_approval_snapshot": "Cash discount above limit"}
        )
        body = invoice._notify_accepted_reviews_body()
        self.assertIn("policy exceptions", body)
        self.assertIn("Cash discount above limit", body)

    # --- D-acessória: write sync on move_line (line 303) ---

    def test_line_discount_change_restarts_validation(self):
        """Writing seller_discount on line with review_ids triggers sync."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        # Create a fake tier review to make review_ids truthy
        self.env["tier.review"].create(
            {
                "model": "account.move",
                "res_id": invoice.id,
                "reviewer_id": self.env.user.id,
                "status": "pending",
                "sequence": 10,
            }
        )
        with patch.object(
            type(invoice), "_sync_discount_validation_state"
        ) as mock_sync:
            inv_line.with_context(
                tr_skip_price_protection=True,
                check_move_validity=False,
            ).write({"seller_discount": 2.0})
            mock_sync.assert_called()


@tagged("post_install", "-at_install")
class TestInvoicePolicyOnchangePaths(CommercialPolicyTestCommon):
    """Category A/C — Onchange/NewId paths."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    # --- onchange condition NewId path (lines 167-176) ---

    def test_onchange_condition_newid_with_condition(self):
        """Onchange condition on NewId applies condition to lines."""
        move_form = Form(
            self.env["account.move"].with_context(default_move_type="out_invoice")
        )
        move_form.partner_id = self.customer
        # The partner onchange should prefill condition
        # Then adding a product line triggers the line onchange
        with move_form.invoice_line_ids.new() as line_form:
            line_form.product_id = self.product_a
            line_form.quantity = 1
        move = move_form.save()
        self.assertTrue(move.commercial_condition_id)

    # --- onchange product applies condition (lines 84, 87-88) ---

    def test_onchange_product_applies_condition(self):
        """_onchange_product_apply_condition fills policy fields."""
        move_form = Form(
            self.env["account.move"].with_context(default_move_type="out_invoice")
        )
        move_form.partner_id = self.customer
        with move_form.invoice_line_ids.new() as line_form:
            line_form.product_id = self.product_a
            line_form.quantity = 1
        move = move_form.save()
        line = move.invoice_line_ids.filtered(
            lambda inv_line: inv_line.display_type == "product"
        )[:1]
        # Policy fields should be populated
        self.assertTrue(line.reference_price or line.base_price)

    # --- _resolve_manual_invoice_agent_commissions guards (lines 320-328) ---

    def test_agent_resolution_skips_without_profile(self):
        """Agent resolution skips lines when profile is missing."""
        invoice = self._create_manual_invoice()
        invoice.write(
            {
                "commercial_condition_id": self.condition.id,
                "sales_profile_id": False,
            }
        )
        # Should not raise
        invoice._resolve_manual_invoice_agent_commissions()

    # --- _apply_condition_to_invoice_line (lines 293-296) ---

    def test_condition_applies_pricing_chain_to_line(self):
        """Direct call to _apply_condition_to_invoice_line fills line."""
        invoice = self._create_manual_invoice()
        invoice.write(
            {
                "commercial_condition_id": self.condition.id,
                "sales_profile_id": self.condition.applicable_profile_id.id,
                "tr_cash_discount": self.condition.cash_discount,
                "tr_fob_discount": self.condition.fob_discount,
                "tr_contractual_return": self.condition.contractual_return,
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
                            "product_id": self.product_a.id,
                            "quantity": 1,
                            "price_unit": 100.0,
                        },
                    )
                ]
            }
        )
        line = invoice.invoice_line_ids.filtered(
            lambda inv_line: inv_line.display_type == "product"
        )[:1]
        invoice._apply_condition_to_invoice_line(line, self.condition)
        self.assertTrue(line.reference_price or line.base_price)

    # --- _recompute_manual_invoice_agents pass (line 304) ---

    def test_recompute_agents_placeholder_noop(self):
        """_recompute_manual_invoice_agents is a no-op placeholder."""
        invoice = self._create_manual_invoice()
        # Should not raise — method body is just `pass`
        invoice._recompute_manual_invoice_agents()

    # --- _resolve_manual_invoice_agent_commissions band_rate False (line 320) ---

    def test_agent_commission_resolution_skips_when_no_band_matches(self):
        """Agent resolution skips line when band_rate is False."""
        invoice = self._create_manual_invoice()
        invoice.write(
            {
                "commercial_condition_id": self.condition.id,
                "sales_profile_id": self.condition.applicable_profile_id.id,
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
                            "product_id": self.product_a.id,
                            "quantity": 1,
                            "price_unit": 100.0,
                            "seller_discount": 0.0,
                        },
                    )
                ]
            }
        )
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        # Mock _get_commission_rate_for_discount to return False
        with patch.object(
            type(inv_line),
            "_get_commission_rate_for_discount",
            return_value=False,
        ):
            invoice._resolve_manual_invoice_agent_commissions()

    # --- _resolve_manual_invoice_agent_commissions no base_commission (line 324) ---

    def test_agent_commission_resolution_skips_without_base_commission(self):
        """Agent resolution skips when agent has no base commission."""
        invoice = self._create_manual_invoice()
        invoice.write(
            {
                "commercial_condition_id": self.condition.id,
                "sales_profile_id": self.condition.applicable_profile_id.id,
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
                            "product_id": self.product_a.id,
                            "quantity": 1,
                            "price_unit": 100.0,
                            "seller_discount": 0.0,
                        },
                    )
                ]
            }
        )
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        # Create agent partner without commission on the partner record
        no_comm_partner = self.env["res.partner"].create(
            {
                "name": "No Commission Partner",
                "agent": True,
                "commission_id": self.commission.id,
            }
        )
        self.env["account.invoice.line.agent"].create(
            {
                "object_id": inv_line.id,
                "agent_id": no_comm_partner.id,
                "commission_id": self.commission.id,
            }
        )
        # Clear partner commission AFTER creating agent_line
        no_comm_partner.commission_id = False
        # line 324: agent_line.agent_id.commission_id is now False → skip
        invoice._resolve_manual_invoice_agent_commissions()

    # --- onchange product_id applies condition (lines 84, 87-88) ---

    def test_product_onchange_fills_policy_fields(self):
        """Direct call to _onchange_product_apply_condition fills fields."""
        invoice = self._create_manual_invoice()
        invoice.write(
            {
                "commercial_condition_id": self.condition.id,
                "sales_profile_id": self.condition.applicable_profile_id.id,
                "tr_cash_discount": self.condition.cash_discount,
                "tr_fob_discount": self.condition.fob_discount,
                "tr_contractual_return": self.condition.contractual_return,
            }
        )
        # Add line with product
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
                            "product_id": self.product_a.id,
                            "quantity": 1,
                            "price_unit": 100.0,
                        },
                    )
                ]
            }
        )
        line = invoice.invoice_line_ids.filtered(
            lambda inv_line: inv_line.display_type == "product"
        )[:1]
        # Directly call the onchange
        line._onchange_product_apply_condition()
        # Should have updated reference_price or base_price
        self.assertTrue(line.reference_price or line.base_price)

    # --- Display computes moved from trento_invoice_usability ---


@tagged("post_install", "-at_install")
class TestInvoicePolicyCommissionFallback(CommercialPolicyTestCommon):
    """Category F — Integration edge cases."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    # --- commission.py: IntegrityError fallback (line 246 — pragma kept) ---

    def test_managed_commission_fallback_finds_existing(self):
        """_find_managed_commission returns existing commission.

        The IntegrityError catch (line 246) is pragma: no cover because
        it requires concurrent transactions to trigger. This test
        verifies the fallback method itself works correctly.
        """
        Commission = self.env["commission"]
        existing = Commission._ensure_managed_commission("open", 10.0)
        found = Commission._find_managed_commission("open", 10.0)
        self.assertEqual(found, existing)


@tagged("post_install", "-at_install")
class TestInvoicePolicyMultiSaleLineGuard(CommercialPolicyTestCommon):
    """Test 1:1 sale_line ↔ invoice_line enforcement."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    def test_snapshot_blocks_multi_sale_line(self):
        """Invoice line with >1 sale_line_ids → UserError on snapshot check."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        # Create a second sale order and link its line to the same invoice line
        order2 = self._create_order()
        order2.fiscal_operation_id = False
        line2 = self._create_order_line(order2, qty=5)
        line2.extra_discount = 0.0
        # Force link (bypass normal flow)
        inv_line.with_context(check_move_validity=False).write(
            {"sale_line_ids": [(4, line2.id)]}
        )
        self.assertGreater(len(inv_line.sale_line_ids), 1)
        with self.assertRaises(UserError):
            invoice._get_invoice_snapshot_issues()

    def test_resync_blocks_multi_sale_line(self):
        """Invoice line with >1 sale_line_ids → UserError on resync."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        order2 = self._create_order()
        order2.fiscal_operation_id = False
        line2 = self._create_order_line(order2, qty=5)
        line2.extra_discount = 0.0
        inv_line.with_context(check_move_validity=False).write(
            {"sale_line_ids": [(4, line2.id)]}
        )
        with self.assertRaises(UserError):
            invoice.action_resync_from_sale_order()

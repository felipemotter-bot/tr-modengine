# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import patch

from odoo import _
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from ..models.policy_utils import resolve_applicable_rule
from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestVolumeQtyResolution(CommercialPolicyTestCommon):
    """Resolution of `tr.sales.profile.rule` with `qty_min` / `qty_uom_id`."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.RuleModel = cls.env["tr.sales.profile.rule"]
        cls.empty = cls.RuleModel
        cls.uom_unit = cls.env.ref("uom.product_uom_unit")
        cls.uom_dozen = cls.env.ref("uom.product_uom_dozen")
        cls.uom_kg = cls.env.ref("uom.product_uom_kgm")
        cls.uom_hour = cls.env.ref("uom.product_uom_hour")

        # Drop placeholder rule of agent_profile so each test can craft
        # a clean rule set. agent_profile is reused per test (TransactionCase
        # rolls back, so cls-level shared state is safe here).
        cls.profile = cls.agent_profile
        cls.profile.rule_ids.unlink()

    # ----- helpers -----

    def _make_rule(self, **kwargs):
        """Create a rule on agent_profile with a permissive commission band.

        Defaults to applied_on='general'. Caller overrides applied_on +
        product/template/category as needed.
        """
        vals = {
            "profile_id": self.profile.id,
            "applied_on": kwargs.pop("applied_on", "general"),
            "commission_band_ids": [
                (0, 0, {"discount_up_to": 100.0, "commission_rate": 0.0}),
            ],
        }
        vals.update(kwargs)
        return self.RuleModel.create(vals)

    def _resolve(self, product, qty, uom):
        return resolve_applicable_rule(
            product, self.profile.rule_ids, self.empty, qty, uom
        )

    # ----- 1. qty_min == 0 baseline -----

    def test_qty_min_zero_behaves_as_today(self):
        rule = self._make_rule(applied_on="product", product_id=self.product_a.id)
        resolved = self._resolve(self.product_a, 1.0, self.uom_unit)
        self.assertEqual(resolved, rule)

    # ----- 2. filter by qty -----

    def test_qty_min_filters_when_below_threshold(self):
        variant_rule = self._make_rule(
            applied_on="product",
            product_id=self.product_a.id,
            qty_min=50.0,
            qty_uom_id=self.uom_unit.id,
        )
        general_rule = self._make_rule(applied_on="general")
        resolved = self._resolve(self.product_a, 30.0, self.uom_unit)
        # variant rule does not pass qty filter → falls back to general
        self.assertEqual(resolved, general_rule)
        self.assertNotEqual(resolved, variant_rule)

    def test_qty_min_passes_when_above_threshold(self):
        variant_rule = self._make_rule(
            applied_on="product",
            product_id=self.product_a.id,
            qty_min=50.0,
            qty_uom_id=self.uom_unit.id,
        )
        self._make_rule(applied_on="general")
        resolved = self._resolve(self.product_a, 70.0, self.uom_unit)
        self.assertEqual(resolved, variant_rule)

    # ----- 3. escalonamento -----

    def test_multiple_rules_same_level_higher_qty_min_wins(self):
        rule_0 = self._make_rule(
            applied_on="product",
            product_id=self.product_a.id,
        )
        rule_50 = self._make_rule(
            applied_on="product",
            product_id=self.product_a.id,
            qty_min=50.0,
            qty_uom_id=self.uom_unit.id,
        )
        rule_100 = self._make_rule(
            applied_on="product",
            product_id=self.product_a.id,
            qty_min=100.0,
            qty_uom_id=self.uom_unit.id,
        )
        # qty=30 → only rule_0 passes
        self.assertEqual(self._resolve(self.product_a, 30.0, self.uom_unit), rule_0)
        # qty=70 → rule_0 + rule_50 pass; rule_50 wins (higher qty_min)
        self.assertEqual(self._resolve(self.product_a, 70.0, self.uom_unit), rule_50)
        # qty=150 → all three pass; rule_100 wins
        self.assertEqual(self._resolve(self.product_a, 150.0, self.uom_unit), rule_100)

    # ----- 4. hierarquia absoluta -----

    def test_hierarchy_absolute_variant_wins_over_template_with_qty_min(self):
        variant_rule = self._make_rule(
            applied_on="product",
            product_id=self.product_a.id,
        )
        self._make_rule(
            applied_on="product_template",
            product_tmpl_id=self.product_template_a.id,
            qty_min=50.0,
            qty_uom_id=self.uom_unit.id,
        )
        # template qty cumprida (qty=70 ≥ 50) mas variant sem qty_min vence
        resolved = self._resolve(self.product_a, 70.0, self.uom_unit)
        self.assertEqual(resolved, variant_rule)

    def test_hierarchy_fallback_when_specific_does_not_pass_qty(self):
        self._make_rule(
            applied_on="product",
            product_id=self.product_a.id,
            qty_min=200.0,
            qty_uom_id=self.uom_unit.id,
        )
        tmpl_rule = self._make_rule(
            applied_on="product_template",
            product_tmpl_id=self.product_template_a.id,
            qty_min=50.0,
            qty_uom_id=self.uom_unit.id,
        )
        # variant não passa (qty=70 < 200) → desce pro template (50 cumprido)
        resolved = self._resolve(self.product_a, 70.0, self.uom_unit)
        self.assertEqual(resolved, tmpl_rule)

    # ----- 5. UoM -----

    def test_uom_conversion_compatible(self):
        rule = self._make_rule(
            applied_on="product",
            product_id=self.product_a.id,
            qty_min=2.0,
            qty_uom_id=self.uom_dozen.id,
        )
        # 24 unidades = 2 dúzias → passa exatamente
        resolved = self._resolve(self.product_a, 24.0, self.uom_unit)
        self.assertEqual(resolved, rule)

    def test_uom_incompatible_ignored(self):
        self._make_rule(
            applied_on="product",
            product_id=self.product_a.id,
            qty_min=10.0,
            qty_uom_id=self.uom_kg.id,
        )
        general_rule = self._make_rule(applied_on="general")
        # uom_hour não é conversível pra Kg → rule ignorada → cai pra general
        resolved = self._resolve(self.product_a, 100.0, self.uom_hour)
        self.assertEqual(resolved, general_rule)

    def test_uom_conversion_float_edge(self):
        """qty_min=12 dúzias com line=144 unidades deve passar mesmo com
        possível ruído de conversão (use float_compare com rounding)."""
        rule = self._make_rule(
            applied_on="product",
            product_id=self.product_a.id,
            qty_min=12.0,
            qty_uom_id=self.uom_dozen.id,
        )
        resolved = self._resolve(self.product_a, 144.0, self.uom_unit)
        self.assertEqual(resolved, rule)

    # ----- 6. category walk -----

    def test_category_walk_child_wins_over_parent_with_qty(self):
        child_rule = self._make_rule(
            applied_on="category",
            categ_id=self.categ_solvents.id,
            qty_min=50.0,
            qty_uom_id=self.uom_unit.id,
        )
        # parent (chemicals) com qty_min maior também cumprido; child vence
        self._make_rule(
            applied_on="category",
            categ_id=self.categ_chemicals.id,
            qty_min=100.0,
            qty_uom_id=self.uom_unit.id,
        )
        resolved = self._resolve(self.product_b, 120.0, self.uom_unit)
        self.assertEqual(resolved, child_rule)

    def test_category_walk_parent_wins_when_child_fails_qty(self):
        self._make_rule(
            applied_on="category",
            categ_id=self.categ_solvents.id,
            qty_min=200.0,
            qty_uom_id=self.uom_unit.id,
        )
        parent_rule = self._make_rule(
            applied_on="category",
            categ_id=self.categ_chemicals.id,
            qty_min=50.0,
            qty_uom_id=self.uom_unit.id,
        )
        # child não passa (qty=70 < 200), sobe pra parent (50 cumprido)
        resolved = self._resolve(self.product_b, 70.0, self.uom_unit)
        self.assertEqual(resolved, parent_rule)

    # ----- 7. assinatura -----

    def test_resolve_signature_requires_qty_args(self):
        self._make_rule(applied_on="general")
        with self.assertRaises(TypeError):
            resolve_applicable_rule(self.product_a, self.profile.rule_ids, self.empty)

    # ----- 8. constraints -----

    def test_check_qty_min_negative_raises(self):
        with self.assertRaises(ValidationError):
            self._make_rule(
                applied_on="general",
                qty_min=-1.0,
            )

    def test_check_qty_min_requires_uom(self):
        with self.assertRaises(ValidationError):
            self._make_rule(
                applied_on="general",
                qty_min=50.0,
            )

    def test_onchange_qty_min_suggests_uom_from_product(self):
        # Use ``new()`` so the qty_min/qty_uom_id transient state does not
        # trigger ``_check_qty_min`` before the onchange has a chance to
        # populate qty_uom_id (the constraint runs on write/flush).
        rule = self.RuleModel.new(
            {
                "profile_id": self.profile.id,
                "applied_on": "product",
                "product_id": self.product_a.id,
                "qty_min": 50.0,
            }
        )
        rule._onchange_qty_min()
        self.assertEqual(rule.qty_uom_id, self.product_a.uom_id)

    def test_onchange_qty_min_suggests_uom_from_template(self):
        rule = self.RuleModel.new(
            {
                "profile_id": self.profile.id,
                "applied_on": "product_template",
                "product_tmpl_id": self.product_template_a.id,
                "qty_min": 50.0,
            }
        )
        rule._onchange_qty_min()
        self.assertEqual(rule.qty_uom_id, self.product_template_a.uom_id)

    def test_onchange_qty_min_warns_when_cannot_infer_uom(self):
        rule = self.RuleModel.new(
            {
                "profile_id": self.profile.id,
                "applied_on": "general",
                "qty_min": 50.0,
            }
        )
        result = rule._onchange_qty_min()
        self.assertIsInstance(result, dict)
        self.assertIn("warning", result)
        self.assertFalse(rule.qty_uom_id)

    # ----- 9. _rule_passes_qty_min defensive paths -----

    def test_passes_qty_min_returns_false_when_line_uom_missing(self):
        self._make_rule(
            applied_on="product",
            product_id=self.product_a.id,
            qty_min=10.0,
            qty_uom_id=self.uom_unit.id,
        )
        general_rule = self._make_rule(applied_on="general")
        # Sem line_uom: rule de variant é ignorada pelo predicate, cai no general
        resolved = self._resolve(self.product_a, 100.0, None)
        self.assertEqual(resolved, general_rule)

    def test_passes_qty_min_user_error_treated_as_ignored(self):
        rule = self._make_rule(
            applied_on="product",
            product_id=self.product_a.id,
            qty_min=2.0,
            qty_uom_id=self.uom_dozen.id,
        )
        general_rule = self._make_rule(applied_on="general")

        def _raise(*args, **kwargs):
            raise UserError(_("forced for test"))

        with patch.object(type(self.uom_unit), "_compute_quantity", _raise):
            resolved = self._resolve(self.product_a, 24.0, self.uom_unit)
        # UserError do _compute_quantity ⇒ rule ignorada ⇒ cai pro general
        self.assertEqual(resolved, general_rule)
        self.assertNotEqual(resolved, rule)

    # ----- 11. invoice compute on non-customer move types -----

    def test_invoice_compute_applied_rule_skipped_for_non_customer_move(self):
        # Vendor bill (in_invoice) should clear applied_rule without calling
        # _get_applicable_rule (covers the early-return branch in the move
        # line compute).
        bill = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "partner_id": self.customer.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "quantity": 5.0,
                            "name": self.product_a.display_name,
                        },
                    ),
                ],
            }
        )
        product_lines = bill.invoice_line_ids.filtered(
            lambda line: line.product_id == self.product_a
        )
        for line in product_lines:
            self.assertFalse(line.applied_rule_id)
            self.assertFalse(line.applied_rule_label)


@tagged("post_install", "-at_install")
class TestVolumeQtyOnSaleAndInvoice(CommercialPolicyTestCommon):
    """Integration: qty_min driving real sale/invoice rule resolution."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_internal_policy()
        cls.uom_unit = cls.env.ref("uom.product_uom_unit")

        # Replace agent profile rules: general (qty_min=0) + variant volume
        cls.agent_profile.rule_ids.unlink()
        cls.general_rule = cls.env["tr.sales.profile.rule"].create(
            {
                "profile_id": cls.agent_profile.id,
                "applied_on": "general",
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 5.0, "commission_rate": 10.0}),
                ],
            }
        )
        cls.volume_rule = cls.env["tr.sales.profile.rule"].create(
            {
                "profile_id": cls.agent_profile.id,
                "applied_on": "product",
                "product_id": cls.product_a.id,
                "qty_min": 50.0,
                "qty_uom_id": cls.uom_unit.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 12.0, "commission_rate": 8.0}),
                ],
            }
        )

    def _new_order(self, qty):
        order = (
            self.env["sale.order"]
            .with_user(self.salesperson)
            .create(
                {
                    "partner_id": self.customer.id,
                    "pricelist_id": self.pricelist.id,
                    "order_line": [
                        (
                            0,
                            0,
                            {
                                "product_id": self.product_a.id,
                                "product_uom_qty": qty,
                                "product_uom": self.uom_unit.id,
                            },
                        ),
                    ],
                }
            )
        )
        return order

    def test_sale_line_resolves_general_when_below_threshold(self):
        order = self._new_order(qty=30.0)
        line = order.order_line
        self.assertEqual(line._get_applicable_rule(), self.general_rule)
        self.assertEqual(line.applied_rule_id, self.general_rule)

    def test_sale_line_resolves_volume_when_above_threshold(self):
        order = self._new_order(qty=70.0)
        line = order.order_line
        self.assertEqual(line._get_applicable_rule(), self.volume_rule)
        self.assertEqual(line.applied_rule_id, self.volume_rule)

    def test_applied_rule_label_includes_volume_segment(self):
        order = self._new_order(qty=70.0)
        label = order.order_line.applied_rule_label
        self.assertIn("Volume", label)
        self.assertIn("50", label)

    def test_label_scope_template(self):
        # Replace the variant volume rule with a template rule so we can
        # exercise the "Template:" branch of _format_applied_rule_label.
        self.volume_rule.unlink()
        self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": self.agent_profile.id,
                "applied_on": "product_template",
                "product_tmpl_id": self.product_template_a.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 8.0, "commission_rate": 9.0}),
                ],
            }
        )
        order = self._new_order(qty=10.0)
        label = order.order_line.applied_rule_label
        self.assertIn("Template", label)

    def test_label_scope_category(self):
        self.volume_rule.unlink()
        self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": self.agent_profile.id,
                "applied_on": "category",
                "categ_id": self.product_a.categ_id.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 7.0, "commission_rate": 9.0}),
                ],
            }
        )
        order = self._new_order(qty=10.0)
        label = order.order_line.applied_rule_label
        self.assertIn("Category", label)

    def test_label_false_for_empty_rule(self):
        # Direct call to _format_applied_rule_label with an empty recordset
        # covers the "if not rule: return False" branch without depending
        # on profile resolution edge cases.
        order = self._new_order(qty=10.0)
        empty = self.env["tr.sales.profile.rule"]
        self.assertFalse(order.order_line._format_applied_rule_label(empty))

    def test_invoice_label_scope_template_and_category(self):
        # Cover the template + category branches in
        # account.move.line._format_applied_rule_label by swapping rules.
        self.volume_rule.unlink()
        tmpl_rule = self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": self.agent_profile.id,
                "applied_on": "product_template",
                "product_tmpl_id": self.product_template_a.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 8.0, "commission_rate": 9.0}),
                ],
            }
        )
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.customer.id,
                "sales_profile_id": self.agent_profile.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "quantity": 5.0,
                            "product_uom_id": self.uom_unit.id,
                            "name": self.product_a.display_name,
                        },
                    ),
                ],
            }
        )
        line = invoice.invoice_line_ids.filtered(
            lambda line: line.product_id == self.product_a
        )
        self.assertIn("Template", line.applied_rule_label)
        # Now swap to category and re-fetch
        tmpl_rule.unlink()
        self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": self.agent_profile.id,
                "applied_on": "category",
                "categ_id": self.product_a.categ_id.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 7.0, "commission_rate": 9.0}),
                ],
            }
        )
        line._compute_applied_rule()
        self.assertIn("Category", line.applied_rule_label)

    def test_invoice_label_false_for_empty_rule(self):
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.customer.id,
                "sales_profile_id": self.agent_profile.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "quantity": 5.0,
                            "name": self.product_a.display_name,
                        },
                    ),
                ],
            }
        )
        line = invoice.invoice_line_ids.filtered(
            lambda line: line.product_id == self.product_a
        )
        empty = self.env["tr.sales.profile.rule"]
        self.assertFalse(line._format_applied_rule_label(empty))

    def test_invoice_line_uses_same_resolution(self):
        # Build a manual invoice on the same partner/profile and assert
        # that resolution mirrors sale.order.line. Salesperson lacks ACL
        # to create account.move; use admin so the test focuses on rule
        # resolution, not on permissions.
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.customer.id,
                "sales_profile_id": self.agent_profile.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "quantity": 70.0,
                            "product_uom_id": self.uom_unit.id,
                            "name": self.product_a.display_name,
                        },
                    ),
                ],
            }
        )
        line = invoice.invoice_line_ids.filtered(
            lambda line: line.product_id == self.product_a
        )
        self.assertEqual(line._get_applicable_rule(), self.volume_rule)
        self.assertEqual(line.applied_rule_id, self.volume_rule)

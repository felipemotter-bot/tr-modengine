# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import UserError

from .common import PricelistReportTestCommon


class TestLayoutHistorico(PricelistReportTestCommon):
    """Customer-history layout (§4.2 of the plan)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.uom_unit = cls.env.ref("uom.product_uom_unit")
        cls.uom_dozen = cls.env.ref("uom.product_uom_dozen")
        cls.env["ir.config_parameter"].sudo().set_param(
            "tr_pricelist_report.history_months_back", "6"
        )

    def _open_history_wizard(self):
        return self.env["tr.pricelist.report.wizard"].create(
            {"condition_id": self.condition.id, "layout": "historico"}
        )

    def _place_confirmed_order(self, product, qty, date_order, uom=None, state="sale"):
        """Create a sale.order with one line and set the required state.

        ``date_order`` is re-applied **after** the state transition because
        ``action_confirm`` / ``action_done`` touch computed fields that can
        refresh ``date_order`` to ``now()``; the test suite needs precise
        control over the window boundary.
        """
        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "pricelist_id": self.pricelist.id,
            }
        )
        self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": product.id,
                "product_uom": (uom or product.uom_id).id,
                "product_uom_qty": qty,
            }
        )
        if state == "sale":
            order.action_confirm()
        elif state == "done":
            order.action_confirm()
            order.action_done()
        elif state == "cancel":
            order.action_cancel()
        order.date_order = fields.Datetime.to_datetime(date_order)
        return order

    # ------------------------------------------------------------------
    # action_generate UserError
    # ------------------------------------------------------------------

    def test_action_generate_raises_when_no_history(self):
        """PDF-empty case surfaces as UserError, not a blank report."""
        wizard = self._open_history_wizard()
        with self.assertRaises(UserError):
            wizard.action_generate()

    # ------------------------------------------------------------------
    # Scope filters
    # ------------------------------------------------------------------

    def test_only_sale_or_done_states_are_counted(self):
        """Draft/sent/cancel orders stay out; sale/done stay in."""
        today = fields.Date.today()
        self._place_confirmed_order(self.product_a, 5, today, state="sale")
        self._place_confirmed_order(self.product_b, 3, today, state="done")
        self._place_confirmed_order(self.product_a, 99, today, state="cancel")
        # Extra draft order (no state transition) — should be ignored.
        self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "pricelist_id": self.pricelist.id,
                "date_order": today,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "product_uom_qty": 50,
                            "product_uom": self.product_a.uom_id.id,
                        },
                    )
                ],
            }
        )
        wizard = self._open_history_wizard()
        totals = wizard._resolve_history_quantities()
        self.assertIn(self.product_a, totals)
        self.assertAlmostEqual(totals[self.product_a], 5.0)
        self.assertIn(self.product_b, totals)
        self.assertAlmostEqual(totals[self.product_b], 3.0)

    def test_orders_outside_window_are_ignored(self):
        """Sales older than ``history_months_back`` don't count."""
        today = fields.Date.today()
        long_ago = today - relativedelta(months=12)
        self._place_confirmed_order(self.product_a, 42, long_ago, state="sale")
        wizard = self._open_history_wizard()
        self.assertNotIn(self.product_a, wizard._resolve_history_quantities())

    def test_archived_product_is_filtered_out(self):
        """Customer bought it but product is ``active=False`` today."""
        today = fields.Date.today()
        self._place_confirmed_order(self.product_a, 5, today, state="sale")
        self.product_a.active = False
        wizard = self._open_history_wizard()
        self.assertNotIn(self.product_a, wizard._resolve_history_quantities())

    def test_non_saleable_product_is_filtered_out(self):
        """``sale_ok=False`` behaves like archived in the history layout."""
        today = fields.Date.today()
        self._place_confirmed_order(self.product_a, 3, today, state="sale")
        self.product_a.sale_ok = False
        wizard = self._open_history_wizard()
        self.assertNotIn(self.product_a, wizard._resolve_history_quantities())

    def test_disabled_history_months_returns_empty(self):
        """``history_months_back=0`` short-circuits the resolver."""
        today = fields.Date.today()
        self._place_confirmed_order(self.product_a, 3, today, state="sale")
        self.env["ir.config_parameter"].sudo().set_param(
            "tr_pricelist_report.history_months_back", "0"
        )
        wizard = self._open_history_wizard()
        self.assertEqual(wizard._resolve_history_quantities(), {})

    # ------------------------------------------------------------------
    # Quantity aggregation
    # ------------------------------------------------------------------

    def test_quantities_aggregate_across_orders(self):
        """Multiple orders on the same product sum their quantities."""
        today = fields.Date.today()
        self._place_confirmed_order(self.product_a, 4, today)
        self._place_confirmed_order(self.product_a, 7, today)
        wizard = self._open_history_wizard()
        self.assertAlmostEqual(
            wizard._resolve_history_quantities()[self.product_a], 11.0
        )

    def test_quantities_convert_different_uoms_to_default(self):
        """Lines in a non-default UoM are converted before aggregation."""
        today = fields.Date.today()
        self.product_a.uom_id = self.uom_unit
        self.product_a.uom_po_id = self.uom_unit
        # 1 dozen = 12 units in the demo ``uom.product_uom_dozen``.
        self._place_confirmed_order(self.product_a, 2, today, uom=self.uom_dozen)
        self._place_confirmed_order(self.product_a, 3, today, uom=self.uom_unit)
        wizard = self._open_history_wizard()
        self.assertAlmostEqual(
            wizard._resolve_history_quantities()[self.product_a], 27.0
        )

    # ------------------------------------------------------------------
    # Exclusion flag interaction (PR3)
    # ------------------------------------------------------------------

    def test_history_ignores_exclude_flag(self):
        """Product in a flagged category still shows up when bought."""
        today = fields.Date.today()
        self._place_confirmed_order(self.product_a, 2, today)
        self.categ_chemicals.tr_exclude_from_general_pricelist = True
        wizard = self._open_history_wizard()
        self.assertIn(self.product_a, wizard._resolve_history_quantities())

    # ------------------------------------------------------------------
    # Sections structure
    # ------------------------------------------------------------------

    def test_sections_group_by_category_and_list_variants(self):
        """Each purchased variant is its own row, grouped by category."""
        today = fields.Date.today()
        self._place_confirmed_order(self.product_a, 2, today)
        self._place_confirmed_order(self.product_b, 4, today)
        wizard = self._open_history_wizard()
        values = wizard._get_report_values(wizard.ids)
        self.assertTrue(values["sections"])
        products_in_report = set()
        for section in values["sections"]:
            for row in section["rows"]:
                products_in_report.add(row["product"])
                self.assertIn("qty", row)
                self.assertIn("uom_label", row)
                self.assertEqual(row["variants"], [])
        self.assertIn(self.product_a, products_in_report)
        self.assertIn(self.product_b, products_in_report)

    def test_exceptions_are_empty_for_history(self):
        """Variant + qty exception sections don't apply to history."""
        today = fields.Date.today()
        self._place_confirmed_order(self.product_a, 1, today)
        wizard = self._open_history_wizard()
        values = wizard._get_report_values(wizard.ids)
        self.assertEqual(values["variant_exceptions"], [])
        self.assertEqual(values["qty_exceptions"], [])

    # ------------------------------------------------------------------
    # Render check (Codex 2026-04-17)
    # ------------------------------------------------------------------

    def test_render_shows_quantity_with_uom_label(self):
        """PDF renders ``qty + uom`` on the Qtd. comprada column."""
        today = fields.Date.today()
        self._place_confirmed_order(self.product_a, 8, today)
        wizard = self._open_history_wizard()
        html, _type = self.env["ir.actions.report"]._render_qweb_html(
            "tr_pricelist_report.action_report_pricelist", wizard.ids
        )
        self.assertIn(b"Qtd. comprada", html)
        uom_label = self.product_a.uom_id.name.encode("utf-8")
        self.assertIn(uom_label, html)

    def test_orders_variants_of_same_template_adjacent(self):
        """Variants of the same template sit next to each other in the row list.

        Sort key `(template.display_name, template.id, product.display_name,
        product.id)` guarantees adjacency even when two templates happen to
        share `display_name` — hence the explicit `template.id` tiebreaker.
        """
        attr = self.env["product.attribute"].create(
            {"name": "Cor", "create_variant": "always"}
        )
        val_a = self.env["product.attribute.value"].create(
            {"name": "Azul", "attribute_id": attr.id}
        )
        val_b = self.env["product.attribute.value"].create(
            {"name": "Verde", "attribute_id": attr.id}
        )
        tmpl_x = self.env["product.template"].create(
            {
                "name": "Tinta",
                "type": "consu",
                "list_price": 10.0,
                "categ_id": self.categ_chemicals.id,
                "attribute_line_ids": [
                    (
                        0,
                        0,
                        {
                            "attribute_id": attr.id,
                            "value_ids": [(6, 0, [val_a.id, val_b.id])],
                        },
                    ),
                ],
            }
        )
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "1_product",
                "product_tmpl_id": tmpl_x.id,
                "compute_price": "fixed",
                "fixed_price": 10.0,
            }
        )
        today = fields.Date.today()
        for variant in tmpl_x.product_variant_ids:
            self._place_confirmed_order(variant, 1, today)
        wizard = self._open_history_wizard()
        values = wizard._get_report_values(wizard.ids)
        # Flatten rows by order and look up the position of each variant.
        all_rows = [row for section in values["sections"] for row in section["rows"]]
        positions = {row["product"].id: idx for idx, row in enumerate(all_rows)}
        pos_list = sorted(
            positions[variant.id] for variant in tmpl_x.product_variant_ids
        )
        self.assertEqual(pos_list[-1] - pos_list[0], len(pos_list) - 1)

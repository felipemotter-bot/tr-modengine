# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields
from odoo.tests.common import tagged

from .common import PricelistReportTestCommon


@tagged("post_install", "-at_install")
class TestFilterDim(PricelistReportTestCommon):
    """``filter_dim`` Selection on the shared pricelist mixin.

    Covers the mutex contract (the picked dimension is the only one
    honored), the ``create`` resolver that infers ``filter_dim`` from
    legacy ``category_ids`` payloads, the onchange that wipes companion
    fields on UI flips, and the new category-aware path inside the
    customer-history layout.
    """

    # ------------------------------------------------------------------
    # create() resolver
    # ------------------------------------------------------------------

    def test_create_resolves_filter_dim_when_category_ids_carry_ids(self):
        """``category_ids`` filled without explicit ``filter_dim`` →
        legacy callers get ``filter_dim="category"`` automatically."""
        wizard = self.env["tr.pricelist.report.wizard"].create(
            {
                "condition_id": self.condition.id,
                "layout": "geral",
                "group_axis": "categoria",
                "category_ids": [(6, 0, [self.categ_chemicals.id])],
            }
        )
        self.assertEqual(wizard.filter_dim, "category")

    def test_create_does_not_resolve_on_empty_category_command(self):
        """Sentinel ``[(5, 0, 0)]`` / ``[(6, 0, [])]`` must not flip the
        dimension — those payloads are equivalent to ``category_ids =
        False`` and the wizard's intent is "no filter"."""
        wizard_clear = self.env["tr.pricelist.report.wizard"].create(
            {
                "condition_id": self.condition.id,
                "category_ids": [(5, 0, 0)],
            }
        )
        self.assertEqual(wizard_clear.filter_dim, "none")
        wizard_empty_six = self.env["tr.pricelist.report.wizard"].create(
            {
                "condition_id": self.condition.id,
                "category_ids": [(6, 0, [])],
            }
        )
        self.assertEqual(wizard_empty_six.filter_dim, "none")

    def test_commands_carry_ids_covers_branches(self):
        """Direct unit coverage of the x2m command sniffer. The full
        ``create`` path tests above exercise codes 5 and 6 (clear /
        bulk replace); this test rounds out 4 (link), 0 (create-and-link)
        and the malformed-command skip branch."""
        Mixin = self.env["tr.pricelist.report.section.builder"]
        self.assertFalse(Mixin._commands_carry_ids(None))
        self.assertFalse(Mixin._commands_carry_ids([]))
        self.assertFalse(Mixin._commands_carry_ids([(5, 0, 0)]))
        self.assertFalse(Mixin._commands_carry_ids([(6, 0, [])]))
        self.assertFalse(Mixin._commands_carry_ids(["bogus", (), None]))
        self.assertTrue(Mixin._commands_carry_ids([(4, 99)]))
        self.assertTrue(Mixin._commands_carry_ids([(0, 0, {"name": "x"})]))
        self.assertTrue(
            Mixin._commands_carry_ids([(5, 0, 0), (4, 42)]),
            "A trailing link command must surface as carrying ids "
            "even when a clear command precedes it.",
        )

    def test_create_respects_explicit_filter_dim(self):
        """An explicit ``filter_dim`` in vals is not overwritten."""
        wizard = self.env["tr.pricelist.report.wizard"].create(
            {
                "condition_id": self.condition.id,
                "filter_dim": "none",
                "category_ids": [(6, 0, [self.categ_chemicals.id])],
            }
        )
        self.assertEqual(wizard.filter_dim, "none")

    # ------------------------------------------------------------------
    # Mutex enforced at execution
    # ------------------------------------------------------------------

    def test_resolve_products_ignores_category_when_filter_dim_none(self):
        """``filter_dim="none"`` is execution-binding: the categories
        list is treated as if empty, even when populated. Solvents
        sits under chemicals, so when the filter would have been on,
        products outside that subtree would be excluded — under
        ``"none"`` they must NOT be."""
        extra_categ = self.env["product.category"].create({"name": "Standalone"})
        extra_product = self.env["product.product"].create(
            {
                "name": "Standalone Product",
                "type": "consu",
                "categ_id": extra_categ.id,
            }
        )
        wizard = self.env["tr.pricelist.report.wizard"].create(
            {
                "condition_id": self.condition.id,
                "filter_dim": "none",
                "category_ids": [(6, 0, [self.categ_chemicals.id])],
            }
        )
        products = wizard._resolve_products(
            category_ids=wizard.category_ids, company_id=False
        )
        self.assertIn(extra_product, products)

    def test_resolve_products_honors_category_when_filter_dim_category(self):
        """Sanity baseline: the existing category filter still works
        when the dimension is explicitly selected."""
        extra_categ = self.env["product.category"].create({"name": "Standalone"})
        extra_product = self.env["product.product"].create(
            {
                "name": "Standalone Product",
                "type": "consu",
                "categ_id": extra_categ.id,
            }
        )
        wizard = self.env["tr.pricelist.report.wizard"].create(
            {
                "condition_id": self.condition.id,
                "filter_dim": "category",
                "category_ids": [(6, 0, [self.categ_chemicals.id])],
            }
        )
        products = wizard._resolve_products(
            category_ids=wizard.category_ids, company_id=False
        )
        self.assertNotIn(extra_product, products)
        self.assertIn(self.product_a, products)
        self.assertIn(self.product_b, products)

    # ------------------------------------------------------------------
    # onchange — UX only, execution mutex is the real guarantee
    # ------------------------------------------------------------------

    def test_onchange_filter_dim_clears_category_ids(self):
        """Flipping ``filter_dim`` off ``category`` empties the
        companion list — UX nicety so the user does not carry stale
        selections across dimensions."""
        wizard = self.env["tr.pricelist.report.wizard"].new(
            {
                "condition_id": self.condition.id,
                "filter_dim": "category",
                "category_ids": [(6, 0, [self.categ_chemicals.id])],
            }
        )
        wizard.filter_dim = "none"
        wizard._onchange_filter_dim()
        self.assertFalse(wizard.category_ids)

    # ------------------------------------------------------------------
    # Customer-history layout — gains category gating
    # ------------------------------------------------------------------

    def _place_confirmed_order(self, product, qty):
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
                "product_uom": product.uom_id.id,
                "product_uom_qty": qty,
            }
        )
        order.action_confirm()
        order.date_order = fields.Datetime.now()
        return order

    def test_history_respects_category_filter_when_dim_is_category(self):
        """Customer bought from both chemicals and a foreign category;
        when the wizard filters by chemicals the foreign-category buy
        is dropped."""
        foreign_categ = self.env["product.category"].create({"name": "Foreign"})
        foreign_product = self.env["product.product"].create(
            {
                "name": "Foreign Product",
                "type": "consu",
                "list_price": 10.0,
                "categ_id": foreign_categ.id,
                "invoice_policy": "order",
            }
        )
        self._place_confirmed_order(self.product_a, 5)
        self._place_confirmed_order(foreign_product, 7)
        wizard = self.env["tr.pricelist.report.wizard"].create(
            {
                "condition_id": self.condition.id,
                "layout": "historico",
                "history_grouping": "variante",
                "filter_dim": "category",
                "category_ids": [(6, 0, [self.categ_chemicals.id])],
            }
        )
        totals = wizard._resolve_history_quantities()
        self.assertIn(self.product_a, totals)
        self.assertNotIn(foreign_product, totals)

    def test_history_ignores_category_when_dim_is_none(self):
        """A stale ``category_ids`` (e.g., persisted from a previous
        wizard run) must NOT silently narrow the history when the
        user moved the dimension back to ``"none"``."""
        foreign_categ = self.env["product.category"].create({"name": "Foreign"})
        foreign_product = self.env["product.product"].create(
            {
                "name": "Foreign Product",
                "type": "consu",
                "list_price": 10.0,
                "categ_id": foreign_categ.id,
                "invoice_policy": "order",
            }
        )
        self._place_confirmed_order(self.product_a, 5)
        self._place_confirmed_order(foreign_product, 7)
        wizard = self.env["tr.pricelist.report.wizard"].create(
            {
                "condition_id": self.condition.id,
                "layout": "historico",
                "history_grouping": "variante",
                "filter_dim": "none",
                "category_ids": [(6, 0, [self.categ_chemicals.id])],
            }
        )
        totals = wizard._resolve_history_quantities()
        self.assertIn(self.product_a, totals)
        self.assertIn(foreign_product, totals)

    def test_history_bypass_of_excluded_flag_still_applies(self):
        """``tr_exclude_from_general_pricelist`` keeps bypassing the
        history layout even with ``filter_dim="category"`` — the flag
        is a basic/geral concern, history is a recompra tool."""
        chemicals = self.categ_chemicals
        chemicals.tr_exclude_from_general_pricelist = True
        self._place_confirmed_order(self.product_a, 4)
        wizard = self.env["tr.pricelist.report.wizard"].create(
            {
                "condition_id": self.condition.id,
                "layout": "historico",
                "history_grouping": "variante",
                "filter_dim": "category",
                "category_ids": [(6, 0, [chemicals.id])],
            }
        )
        totals = wizard._resolve_history_quantities()
        self.assertIn(self.product_a, totals)

    # ------------------------------------------------------------------
    # Hook contract — downstream-friendly defaults
    # ------------------------------------------------------------------

    def test_default_hooks_return_empty_list(self):
        """Both domain hooks must default to an empty list so
        downstream modules can ``super()`` and concatenate."""
        wizard = self.env["tr.pricelist.basic.wizard"].create(
            {
                "company_id": self.env.company.id,
                "pricelist_id": self.pricelist.id,
            }
        )
        self.assertEqual(wizard._get_extra_product_domain(), [])
        self.assertEqual(wizard._get_extra_history_line_domain(), [])

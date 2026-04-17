# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import PricelistReportTestCommon


class TestLayoutPorCategoria(PricelistReportTestCommon):
    def test_sections_by_category_recursive(self):
        """Picking a parent category returns products of its children too.

        With default ``category_depth=-2`` (parent of leaf), both
        ``product_a`` (categ_id = Chemicals, a root) and ``product_b``
        (categ_id = Solvents, child of Chemicals) end up under
        ``Chemicals`` — the clamping rule resolves Chemicals itself as
        the closest available ancestor for both.
        """
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        values = wizard._get_report_values(wizard.ids)
        section_titles = [s["title"] for s in values["sections"]]
        self.assertIn(self.categ_chemicals.name, section_titles)
        # Both templates end up under the same section with depth=-2.
        chemicals_section = next(
            s for s in values["sections"] if s["title"] == self.categ_chemicals.name
        )
        row_templates = {row["template"].id for row in chemicals_section["rows"]}
        self.assertIn(self.product_template_a.id, row_templates)
        self.assertIn(self.product_template_b.id, row_templates)

    def test_sections_split_when_depth_is_leaf(self):
        """Setting ``category_depth=-1`` puts each categ_id in its own bucket."""
        self.env["ir.config_parameter"].sudo().set_param(
            "tr_pricelist_report.category_depth", "-1"
        )
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        values = wizard._get_report_values(wizard.ids)
        section_titles = [s["title"] for s in values["sections"]]
        self.assertIn(self.categ_chemicals.name, section_titles)
        self.assertIn(self.categ_solvents.name, section_titles)

    def test_template_row_fields(self):
        """Each row carries template, pricing and variants info."""
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        values = wizard._get_report_values(wizard.ids)
        all_rows = [row for section in values["sections"] for row in section["rows"]]
        self.assertTrue(all_rows)
        for row in all_rows:
            self.assertIn("template", row)
            self.assertIn("pricing", row)
            self.assertIn("variants", row)
            self.assertIn("base", row["pricing"])
            self.assertIn("reference", row["pricing"])
            self.assertIn("price_unit", row["pricing"])

    def test_empty_category_produces_no_rows(self):
        """Category without sale_ok products returns empty sections."""
        empty_categ = self.env["product.category"].create({"name": "Empty"})
        wizard = self._open_wizard(category_ids=[empty_categ.id])
        values = wizard._get_report_values(wizard.ids)
        self.assertEqual(values["sections"], [])

    def test_archived_products_excluded(self):
        """Archived products are not included."""
        self.product_a.active = False
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        values = wizard._get_report_values(wizard.ids)
        all_products = [
            row["template"].id
            for section in values["sections"]
            for row in section["rows"]
        ]
        self.assertNotIn(self.product_template_a.id, all_products)

    def test_non_saleable_products_excluded(self):
        """Products with sale_ok=False are excluded."""
        self.product_a.sale_ok = False
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        values = wizard._get_report_values(wizard.ids)
        all_products = [
            row["template"].id
            for section in values["sections"]
            for row in section["rows"]
        ]
        self.assertNotIn(self.product_template_a.id, all_products)

    def test_no_categories_returns_empty_categories_queryset(self):
        """``_expand_categories`` returns empty recordset when none picked."""
        wizard = self.env["tr.pricelist.report.wizard"].create(
            {"condition_id": self.condition.id}
        )
        self.assertFalse(wizard._expand_categories())

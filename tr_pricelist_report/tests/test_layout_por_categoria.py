# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import PricelistReportTestCommon


class TestLayoutPorCategoria(PricelistReportTestCommon):
    def test_sections_by_category_recursive(self):
        """Picking a parent category returns products of its children too."""
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        values = wizard._get_report_values(wizard.ids)
        section_titles = [s["title"] for s in values["sections"]]
        # Both chemicals (product A) and solvents (product B, child) present
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

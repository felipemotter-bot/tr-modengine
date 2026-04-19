# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError

from .common import PricelistReportTestCommon


class TestLayoutGeral(PricelistReportTestCommon):
    """Cover the General Pricelist layout in both axes (MARCA and category).

    After PR-C, this is the only general layout in the wizard —
    ``por_categoria`` was dropped and its behavior now lives under
    ``layout='geral' + group_axis='categoria'``. Tests migrated from
    the old ``test_layout_por_categoria.py`` are below, marked with
    the "category axis" comment blocks.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Build a MARCA attribute with two values + a template whose variants
        # fan out by MARCA (mimics the real "Velas Santa Paulina / D'Guarda /
        # Trento" structure without leaking the real brand names).
        cls.attr_marca = cls.env["product.attribute"].create(
            {"name": "MARCA", "create_variant": "always"}
        )
        cls.val_alpha = cls.env["product.attribute.value"].create(
            {"name": "Alpha Brand", "attribute_id": cls.attr_marca.id}
        )
        cls.val_beta = cls.env["product.attribute.value"].create(
            {"name": "Beta Brand", "attribute_id": cls.attr_marca.id}
        )
        cls.branded_template = cls.env["product.template"].create(
            {
                "name": "Branded Product",
                "type": "consu",
                "list_price": 100.0,
                "categ_id": cls.categ_chemicals.id,
                "attribute_line_ids": [
                    (
                        0,
                        0,
                        {
                            "attribute_id": cls.attr_marca.id,
                            "value_ids": [(6, 0, [cls.val_alpha.id, cls.val_beta.id])],
                        },
                    ),
                ],
            }
        )
        cls.env["product.pricelist.item"].create(
            {
                "pricelist_id": cls.pricelist.id,
                "applied_on": "1_product",
                "product_tmpl_id": cls.branded_template.id,
                "compute_price": "fixed",
                "fixed_price": 100.0,
            }
        )
        # Point the attribute discovery parameter at this attribute so
        # ``_get_group_attribute`` returns it without re-running the hook.
        cls.env["ir.config_parameter"].sudo().set_param(
            "tr_pricelist_report.group_attribute_id", str(cls.attr_marca.id)
        )

    def _open_geral(self, group_axis="marca"):
        return self.env["tr.pricelist.report.wizard"].create(
            {
                "condition_id": self.condition.id,
                "layout": "geral",
                "group_axis": group_axis,
            }
        )

    # ------------------------------------------------------------------
    # Brand axis (MARCA)
    # ------------------------------------------------------------------

    def test_mode_a_partitions_by_marca_value(self):
        """Each MARCA value becomes its own section."""
        wizard = self._open_geral(group_axis="marca")
        values = wizard._get_report_values(wizard.ids)
        titles = [s["title"] for s in values["sections"]]
        self.assertIn("Alpha Brand", titles)
        self.assertIn("Beta Brand", titles)

    def test_mode_a_products_without_marca_fallback_to_category(self):
        """``product_a`` has no MARCA attribute — falls back by category."""
        wizard = self._open_geral(group_axis="marca")
        values = wizard._get_report_values(wizard.ids)
        titles = [s["title"] for s in values["sections"]]
        # product_a is in categ_chemicals (root, default depth=-2 clamps
        # to root itself).
        self.assertIn(self.categ_chemicals.name, titles)
        # And the fallback section comes AFTER the branded sections.
        marca_positions = [
            i
            for i, s in enumerate(values["sections"])
            if s["title"] in ("Alpha Brand", "Beta Brand")
        ]
        category_positions = [
            i
            for i, s in enumerate(values["sections"])
            if s["title"] == self.categ_chemicals.name
        ]
        self.assertTrue(marca_positions)
        self.assertTrue(category_positions)
        self.assertLess(max(marca_positions), min(category_positions))

    def test_mode_a_with_unresolved_attribute_falls_back_completely(self):
        """No configured attribute → every product routed through category."""
        self.env["ir.config_parameter"].sudo().set_param(
            "tr_pricelist_report.group_attribute_id", ""
        )
        wizard = self._open_geral(group_axis="marca")
        values = wizard._get_report_values(wizard.ids)
        titles = [s["title"] for s in values["sections"]]
        self.assertNotIn("Alpha Brand", titles)
        self.assertNotIn("Beta Brand", titles)
        self.assertIn(self.categ_chemicals.name, titles)

    def test_mode_a_empty_marca_section_is_dropped(self):
        """A MARCA whose every product got filtered by invalid_price_threshold
        is skipped — no ghost section with an empty table.

        Covers ``_build_sections_by_marca`` skip branch (mirror of the
        ``_build_sections_by_category`` guard).
        """
        val_gamma = self.env["product.attribute.value"].create(
            {"name": "Gamma Brand", "attribute_id": self.attr_marca.id}
        )
        placeholder_branded_tmpl = self.env["product.template"].create(
            {
                "name": "Gamma Placeholder",
                "type": "consu",
                "list_price": 999999.0,
                "categ_id": self.categ_chemicals.id,
                "attribute_line_ids": [
                    (
                        0,
                        0,
                        {
                            "attribute_id": self.attr_marca.id,
                            "value_ids": [(6, 0, [val_gamma.id])],
                        },
                    ),
                ],
            }
        )
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "1_product",
                "product_tmpl_id": placeholder_branded_tmpl.id,
                "compute_price": "fixed",
                "fixed_price": 999999.0,
            }
        )
        wizard = self._open_geral(group_axis="marca")
        values = wizard._get_report_values(wizard.ids)
        titles = [s["title"] for s in values["sections"]]
        # Gamma is dropped; Alpha/Beta still there (they have a valid price).
        self.assertNotIn("Gamma Brand", titles)
        self.assertIn("Alpha Brand", titles)
        self.assertIn("Beta Brand", titles)

    def test_action_generate_no_categories_ok_for_geral(self):
        """``category_ids`` is optional when layout is General Pricelist."""
        wizard = self._open_geral(group_axis="marca")
        # Must not raise despite empty category_ids.
        action = wizard.action_generate()
        self.assertEqual(action["type"], "ir.actions.report")

    # ------------------------------------------------------------------
    # Category axis — migrated from the old test_layout_por_categoria.py
    # when that layout was dropped.
    # ------------------------------------------------------------------

    def test_mode_b_partitions_by_category(self):
        """``group_axis='categoria'`` groups every product by category depth."""
        wizard = self._open_geral(group_axis="categoria")
        values = wizard._get_report_values(wizard.ids)
        titles = [s["title"] for s in values["sections"]]
        # Branded product's categ_id is Chemicals (root). Default depth=-2
        # clamps to the root itself for both Chemicals- and Solvents-based
        # products, so we expect a single "Chemicals" section.
        self.assertIn(self.categ_chemicals.name, titles)
        # No MARCA-based section in Mode B.
        self.assertNotIn("Alpha Brand", titles)
        self.assertNotIn("Beta Brand", titles)

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

    def test_empty_category_raises_user_error(self):
        """Empty scope bubbles up as a friendly UserError, not a blank PDF."""
        empty_categ = self.env["product.category"].create({"name": "Empty"})
        wizard = self._open_wizard(category_ids=[empty_categ.id])
        with self.assertRaises(UserError):
            wizard._get_report_values(wizard.ids)

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
            {"condition_id": self.condition.id, "layout": "geral"}
        )
        self.assertFalse(wizard._expand_categories())

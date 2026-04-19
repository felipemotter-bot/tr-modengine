# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import PricelistReportTestCommon


class TestLayoutCompleta(PricelistReportTestCommon):
    """Cover the Complete Pricelist layout in both axes (MARCA and category)."""

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

    def _open_completa(self, group_axis="marca"):
        return self.env["tr.pricelist.report.wizard"].create(
            {
                "condition_id": self.condition.id,
                "layout": "completa",
                "group_axis": group_axis,
            }
        )

    def test_mode_a_partitions_by_marca_value(self):
        """Each MARCA value becomes its own section."""
        wizard = self._open_completa(group_axis="marca")
        values = wizard._get_report_values(wizard.ids)
        titles = [s["title"] for s in values["sections"]]
        self.assertIn("Alpha Brand", titles)
        self.assertIn("Beta Brand", titles)

    def test_mode_a_products_without_marca_fallback_to_category(self):
        """``product_a`` has no MARCA attribute — falls back by category."""
        wizard = self._open_completa(group_axis="marca")
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

    def test_mode_b_partitions_by_category(self):
        """``group_axis='categoria'`` groups every product by category depth."""
        wizard = self._open_completa(group_axis="categoria")
        values = wizard._get_report_values(wizard.ids)
        titles = [s["title"] for s in values["sections"]]
        # Branded product's categ_id is Chemicals (root). Default depth=-2
        # clamps to the root itself for both Chemicals- and Solvents-based
        # products, so we expect a single "Chemicals" section.
        self.assertIn(self.categ_chemicals.name, titles)
        # No MARCA-based section in Mode B.
        self.assertNotIn("Alpha Brand", titles)
        self.assertNotIn("Beta Brand", titles)

    def test_mode_a_with_unresolved_attribute_falls_back_completely(self):
        """No configured attribute → every product routed through category."""
        self.env["ir.config_parameter"].sudo().set_param(
            "tr_pricelist_report.group_attribute_id", ""
        )
        wizard = self._open_completa(group_axis="marca")
        values = wizard._get_report_values(wizard.ids)
        titles = [s["title"] for s in values["sections"]]
        self.assertNotIn("Alpha Brand", titles)
        self.assertNotIn("Beta Brand", titles)
        self.assertIn(self.categ_chemicals.name, titles)

    def test_action_generate_no_categories_ok_for_completa(self):
        """``category_ids`` is optional when layout is Complete Pricelist."""
        wizard = self._open_completa(group_axis="marca")
        # Must not raise despite empty category_ids.
        action = wizard.action_generate()
        self.assertEqual(action["type"], "ir.actions.report")

    def test_mode_a_empty_marca_section_is_dropped(self):
        """A MARCA whose every product got filtered by invalid_price_threshold
        is skipped — no ghost section with an empty table.

        Covers ``_build_sections_by_marca`` skip branch (mirror of the
        ``_build_sections_by_category`` guard). Felipe hit this in devel:
        printing the layout listed several marca/category titles with no
        rows underneath because the whole bucket was above the 999999
        placeholder threshold.
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
        wizard = self._open_completa(group_axis="marca")
        values = wizard._get_report_values(wizard.ids)
        titles = [s["title"] for s in values["sections"]]
        # Gamma is dropped; Alpha/Beta still there (they have a valid price).
        self.assertNotIn("Gamma Brand", titles)
        self.assertIn("Alpha Brand", titles)
        self.assertIn("Beta Brand", titles)

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError

from .common import PricelistReportTestCommon


class TestBasicWizard(PricelistReportTestCommon):
    """Cover the Basic Pricelist wizard (``tr.pricelist.basic.wizard``).

    Focus areas:
    - Sections via the shared section builder (MARCA + categoria axes).
    - Raw pricelist price via ``_get_product_price(partner=False)``.
    - Simulated contractual return as a flat multiplier on ``price_unit``.
    - Company scope: pricelist/product pinned to the wizard's company.
    - Category exclusion flag honored equally with the General layout.
    - Chained pricelist (``base_pricelist_id``) still resolves.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # MARCA attribute, shared setup mirrors test_layout_geral.py.
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
        cls.env["ir.config_parameter"].sudo().set_param(
            "tr_pricelist_report.group_attribute_id", str(cls.attr_marca.id)
        )

    def _open_basic(self, **vals):
        defaults = {
            "pricelist_id": self.pricelist.id,
            "group_axis": "marca",
        }
        defaults.update(vals)
        return self.env["tr.pricelist.basic.wizard"].create(defaults)

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    def test_basic_wizard_marca_axis(self):
        """``group_axis='marca'`` builds one section per MARCA value."""
        wizard = self._open_basic(group_axis="marca")
        values = wizard._get_report_values(wizard.ids)
        titles = [s["title"] for s in values["sections"]]
        self.assertIn("Alpha Brand", titles)
        self.assertIn("Beta Brand", titles)

    def test_basic_wizard_categoria_axis(self):
        """``group_axis='categoria'`` groups every product by category depth."""
        wizard = self._open_basic(group_axis="categoria")
        values = wizard._get_report_values(wizard.ids)
        titles = [s["title"] for s in values["sections"]]
        self.assertIn(self.categ_chemicals.name, titles)
        self.assertNotIn("Alpha Brand", titles)

    def test_basic_wizard_empty_category_scope_prints_all(self):
        """Empty ``category_ids`` prints the whole sellable scope."""
        wizard = self._open_basic(group_axis="categoria")
        values = wizard._get_report_values(wizard.ids)
        all_templates = set()
        for section in values["sections"]:
            all_templates.update(row["template"].id for row in section["rows"])
        self.assertIn(self.product_template_a.id, all_templates)
        self.assertIn(self.product_template_b.id, all_templates)

    def test_basic_wizard_filters_by_category_when_populated(self):
        """Picking a category restricts the scope to it (and descendants)."""
        wizard = self._open_basic(
            group_axis="categoria",
            category_ids=[(6, 0, [self.categ_solvents.id])],
        )
        values = wizard._get_report_values(wizard.ids)
        all_templates = set()
        for section in values["sections"]:
            all_templates.update(row["template"].id for row in section["rows"])
        self.assertIn(self.product_template_b.id, all_templates)
        self.assertNotIn(self.product_template_a.id, all_templates)

    def test_basic_wizard_archived_excluded(self):
        """Archived products are not included."""
        self.product_a.active = False
        wizard = self._open_basic(group_axis="categoria")
        values = wizard._get_report_values(wizard.ids)
        all_templates = set()
        for section in values["sections"]:
            all_templates.update(row["template"].id for row in section["rows"])
        self.assertNotIn(self.product_template_a.id, all_templates)

    def test_basic_wizard_non_saleable_excluded(self):
        """Products with ``sale_ok=False`` are excluded."""
        self.product_a.sale_ok = False
        wizard = self._open_basic(group_axis="categoria")
        values = wizard._get_report_values(wizard.ids)
        all_templates = set()
        for section in values["sections"]:
            all_templates.update(row["template"].id for row in section["rows"])
        self.assertNotIn(self.product_template_a.id, all_templates)

    def test_basic_wizard_empty_scope_raises_user_error(self):
        """Empty scope bubbles up as a friendly UserError, not a blank PDF."""
        empty_categ = self.env["product.category"].create({"name": "Empty"})
        wizard = self._open_basic(
            group_axis="categoria",
            category_ids=[(6, 0, [empty_categ.id])],
        )
        with self.assertRaises(UserError):
            wizard._get_report_values(wizard.ids)

    def test_basic_wizard_respects_exclusion_flag(self):
        """Categories flagged ``tr_exclude_from_general_pricelist`` are dropped.

        Same guard as General layout — the Basic layout is a "tipo-geral"
        printout and must honor the cascade.
        """
        self.categ_solvents.tr_exclude_from_general_pricelist = True
        wizard = self._open_basic(group_axis="categoria")
        values = wizard._get_report_values(wizard.ids)
        all_templates = set()
        for section in values["sections"]:
            all_templates.update(row["template"].id for row in section["rows"])
        self.assertNotIn(self.product_template_b.id, all_templates)
        self.assertIn(self.product_template_a.id, all_templates)

    # ------------------------------------------------------------------
    # Pricing resolver
    # ------------------------------------------------------------------

    def test_basic_pricing_resolver_keys(self):
        """Basic resolver returns the full shared pricing contract."""
        wizard = self._open_basic()
        pricing = wizard._resolve_basic_pricing(self.product_a)
        for key in (
            "product",
            "base",
            "reference",
            "price_unit",
            "seller_discount",
            "simulated_contractual_return",
        ):
            self.assertIn(key, pricing)

    def test_basic_pricing_resolver_raw_pricelist(self):
        """``seller_discount=0`` and ``price_unit == base`` when no simulation."""
        wizard = self._open_basic(simulated_contractual_return=0.0)
        pricing = wizard._resolve_basic_pricing(self.product_a)
        self.assertEqual(pricing["seller_discount"], 0.0)
        self.assertEqual(pricing["simulated_contractual_return"], 0.0)
        self.assertAlmostEqual(pricing["base"], 100.0, places=2)
        self.assertAlmostEqual(pricing["price_unit"], 100.0, places=2)
        self.assertAlmostEqual(pricing["reference"], 100.0, places=2)

    def test_basic_wizard_simulated_return_applies(self):
        """``simulated_contractual_return=10`` → ``price_unit == base * 0.9``."""
        wizard = self._open_basic(simulated_contractual_return=10.0)
        pricing = wizard._resolve_basic_pricing(self.product_a)
        self.assertAlmostEqual(pricing["base"], 100.0, places=2)
        self.assertAlmostEqual(pricing["reference"], 100.0, places=2)
        self.assertAlmostEqual(pricing["simulated_contractual_return"], 10.0, places=2)
        self.assertAlmostEqual(pricing["price_unit"], 90.0, places=2)

    def test_basic_wizard_chained_pricelist_resolves(self):
        """Pricelist with ``base_pricelist_id`` chain still returns the
        inherited price — proves ``_get_product_price(partner=False)`` is
        the right API and doesn't short-circuit the inheritance.
        """
        derived_pricelist = self.env["product.pricelist"].create(
            {
                "name": "Derived",
                "currency_id": self.env.ref("base.BRL").id,
                "item_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "3_global",
                            "compute_price": "formula",
                            "base": "pricelist",
                            "base_pricelist_id": self.pricelist.id,
                            "price_discount": 0.0,
                        },
                    )
                ],
            }
        )
        wizard = self._open_basic(pricelist_id=derived_pricelist.id)
        pricing = wizard._resolve_basic_pricing(self.product_a)
        # Base pricelist item sets Product A at 100 → derived must echo it.
        self.assertAlmostEqual(pricing["base"], 100.0, places=2)

    # ------------------------------------------------------------------
    # Report entry point
    # ------------------------------------------------------------------

    def test_basic_action_generate_returns_report(self):
        """``action_generate`` returns the basic report action."""
        wizard = self._open_basic()
        action = wizard.action_generate()
        self.assertEqual(action["type"], "ir.actions.report")
        self.assertEqual(
            action["report_name"],
            "tr_pricelist_report.report_pricelist_basic_document",
        )

    def test_basic_report_renders_html(self):
        """End-to-end QWeb render goes through the adapter model.

        Guards against a missing ``report.<report_name>`` adapter: if
        the engine falls back to the generic rendering context, the
        template vars (``pricelist``, ``sections``, ...) come out
        undefined and the render raises.
        """
        wizard = self._open_basic(simulated_contractual_return=10.0)
        report = self.env.ref("tr_pricelist_report.action_report_pricelist_basic")
        html, _content_type = report._render_qweb_html(report.report_name, wizard.ids)
        html_text = html.decode() if isinstance(html, bytes) else html
        self.assertIn("TABELA BÁSICA", html_text)
        self.assertIn("Ret. Sim. %", html_text)

    def test_basic_wizard_no_condition_needed(self):
        """No ``condition_id`` field on the Basic wizard — creation succeeds."""
        wizard = self.env["tr.pricelist.basic.wizard"].create(
            {"pricelist_id": self.pricelist.id}
        )
        # Spot-check: the field isn't part of the model.
        self.assertNotIn("condition_id", wizard._fields)

    # ------------------------------------------------------------------
    # Multi-company
    # ------------------------------------------------------------------

    def test_basic_wizard_shared_pricelist_allowed(self):
        """Pricelist with ``company_id=False`` (shared) passes the check."""
        wizard = self._open_basic()
        # Default ``pricelist.company_id`` is False — creation must succeed
        # and the company scope check on create/write is not triggered.
        self.assertFalse(wizard.pricelist_id.company_id)

    def test_basic_wizard_other_company_pricelist_rejected(self):
        """A pricelist pinned to another company triggers ``_check_company``.

        ``_check_company_auto=True`` on the model plus ``check_company=True``
        on ``pricelist_id`` make the framework validate that the picked
        pricelist belongs to ``company_id`` (or is shared). The validator
        raises ``UserError`` at create/write time.
        """
        other_company = self.env["res.company"].create({"name": "Other Co"})
        foreign_pricelist = self.env["product.pricelist"].create(
            {
                "name": "Foreign",
                "currency_id": self.env.ref("base.BRL").id,
                "company_id": other_company.id,
            }
        )
        with self.assertRaises(UserError):
            self.env["tr.pricelist.basic.wizard"].create(
                {
                    "company_id": self.env.company.id,
                    "pricelist_id": foreign_pricelist.id,
                }
            )

    def test_basic_wizard_other_company_product_filtered(self):
        """Products pinned to another company are dropped from the scope."""
        other_company = self.env["res.company"].create({"name": "Other Co Products"})
        foreign_template = self.env["product.template"].create(
            {
                "name": "Foreign Product",
                "type": "consu",
                "categ_id": self.categ_chemicals.id,
                "company_id": other_company.id,
                "list_price": 50.0,
            }
        )
        wizard = self._open_basic(group_axis="categoria")
        values = wizard._get_report_values(wizard.ids)
        all_templates = set()
        for section in values["sections"]:
            all_templates.update(row["template"].id for row in section["rows"])
        self.assertNotIn(foreign_template.id, all_templates)

    # ------------------------------------------------------------------
    # ``pricelist_id`` default — first by sequence in current company
    # ------------------------------------------------------------------

    def test_default_pricelist_picks_lowest_sequence(self):
        """Default ``pricelist_id`` is the lowest-sequence pricelist in scope.

        Mirrors the Odoo core behavior of attributing a default pricelist
        to a freshly created customer (the one at the top of the
        Pricelists tree view). Assertion is on the invariant (no other
        pricelist in scope has a lower sequence) so the fixture isn't
        coupled to whatever demo data the base ships with.
        """
        company = self.env.company
        # Add a high-sequence pricelist that should NOT be picked.
        self.env["product.pricelist"].create(
            {
                "name": "Z Last",
                "currency_id": self.env.ref("base.BRL").id,
                "company_id": company.id,
                "sequence": 9999,
            }
        )
        wizard = self.env["tr.pricelist.basic.wizard"].create({})
        self.assertTrue(wizard.pricelist_id)
        in_scope = self.env["product.pricelist"].search(
            [("company_id", "in", (company.id, False))]
        )
        min_sequence = min(in_scope.mapped("sequence"))
        self.assertEqual(wizard.pricelist_id.sequence, min_sequence)

    def test_default_pricelist_ignores_other_company(self):
        """Pricelists pinned to a foreign company are skipped by the default."""
        company = self.env.company
        other_company = self.env["res.company"].create({"name": "Foreign Co"})
        # Foreign pricelist with the lowest sequence — must be ignored.
        self.env["product.pricelist"].create(
            {
                "name": "Foreign Top",
                "currency_id": self.env.ref("base.BRL").id,
                "company_id": other_company.id,
                "sequence": 1,
            }
        )
        # Local pricelist that the default should pick.
        local = self.env["product.pricelist"].create(
            {
                "name": "Local Only",
                "currency_id": self.env.ref("base.BRL").id,
                "company_id": company.id,
                "sequence": 50,
            }
        )
        wizard = self.env["tr.pricelist.basic.wizard"].create({})
        # Whatever is picked must belong to the current company (or be
        # shared); never the foreign one.
        self.assertIn(wizard.pricelist_id.company_id.id, (False, company.id))
        # And specifically: when no shared has a lower sequence than the
        # local, the local is picked.
        if wizard.pricelist_id != local:
            self.assertLessEqual(wizard.pricelist_id.sequence, local.sequence)
        self.assertNotEqual(wizard.pricelist_id.company_id, other_company)

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields

from .common import PricelistReportTestCommon


class TestLayoutHistoricoTemplateGrouping(PricelistReportTestCommon):
    """Customer-history layout, ``history_grouping='template'`` mode.

    Variants of the same template with the same price collapse into a
    single template row; divergent variants surface as
    ``variant_exceptions``. The template row's ``qty`` is the sum
    across all valid variants of that template.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(
            "tr_pricelist_report.history_months_back", "6"
        )
        attr = cls.env["product.attribute"].create(
            {"name": "Cor", "create_variant": "always"}
        )
        cls.color_red = cls.env["product.attribute.value"].create(
            {"name": "Vermelho", "attribute_id": attr.id}
        )
        cls.color_blue = cls.env["product.attribute.value"].create(
            {"name": "Azul", "attribute_id": attr.id}
        )
        cls.color_green = cls.env["product.attribute.value"].create(
            {"name": "Verde", "attribute_id": attr.id}
        )
        cls.multi_template = cls.env["product.template"].create(
            {
                "name": "Vela Colorida",
                "type": "consu",
                "list_price": 50.0,
                "categ_id": cls.categ_chemicals.id,
                "attribute_line_ids": [
                    (
                        0,
                        0,
                        {
                            "attribute_id": attr.id,
                            "value_ids": [
                                (
                                    6,
                                    0,
                                    [
                                        cls.color_red.id,
                                        cls.color_blue.id,
                                        cls.color_green.id,
                                    ],
                                )
                            ],
                        },
                    )
                ],
            }
        )
        cls.env["product.pricelist.item"].create(
            {
                "pricelist_id": cls.pricelist.id,
                "applied_on": "1_product",
                "product_tmpl_id": cls.multi_template.id,
                "compute_price": "fixed",
                "fixed_price": 50.0,
            }
        )
        variants = cls.multi_template.product_variant_ids
        cls.variant_red = variants.filtered(
            lambda v: cls.color_red
            in v.product_template_attribute_value_ids.mapped(
                "product_attribute_value_id"
            )
        )
        cls.variant_blue = variants.filtered(
            lambda v: cls.color_blue
            in v.product_template_attribute_value_ids.mapped(
                "product_attribute_value_id"
            )
        )
        cls.variant_green = variants.filtered(
            lambda v: cls.color_green
            in v.product_template_attribute_value_ids.mapped(
                "product_attribute_value_id"
            )
        )

    def _open_template_wizard(self):
        return self.env["tr.pricelist.report.wizard"].create(
            {
                "condition_id": self.condition.id,
                "layout": "historico",
                "history_grouping": "template",
            }
        )

    def _place_order(self, product, qty):
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
        order.date_order = fields.Datetime.to_datetime(fields.Date.today())
        return order

    # ------------------------------------------------------------------
    # Defaults
    # ------------------------------------------------------------------

    def test_wizard_defaults_to_historico_template(self):
        """New wizard opens with layout=historico, grouping=template."""
        wizard = self.env["tr.pricelist.report.wizard"].create(
            {"condition_id": self.condition.id}
        )
        self.assertEqual(wizard.layout, "historico")
        self.assertEqual(wizard.history_grouping, "template")

    # ------------------------------------------------------------------
    # Same price → collapse
    # ------------------------------------------------------------------

    def test_same_price_variants_collapse_to_template_row(self):
        """All variants share the same price → one template row, qty=sum."""
        self._place_order(self.variant_red, 3)
        self._place_order(self.variant_blue, 4)
        self._place_order(self.variant_green, 5)
        wizard = self._open_template_wizard()
        values = wizard._get_report_values(wizard.ids)
        template_rows = [
            row
            for section in values["sections"]
            for row in section["rows"]
            if row["template"] == self.multi_template
        ]
        self.assertEqual(len(template_rows), 1)
        self.assertAlmostEqual(template_rows[0]["qty"], 12.0)
        self.assertEqual(values["variant_exceptions"], [])

    # ------------------------------------------------------------------
    # Divergent variant → exception
    # ------------------------------------------------------------------

    def test_divergent_variant_emits_inline_exception(self):
        """Variant with its own price line surfaces as inline exception.

        The template row's qty includes the divergent variant's qty too
        — the customer sees the total movement of the template family.
        Exception row sits as a sub-row of the template
        (``inline_exceptions``), not in the global
        ``variant_exceptions`` list.
        """
        # Per-variant override on red so its price diverges from the
        # template-level fixed_price (45 vs 50).
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "0_product_variant",
                "product_id": self.variant_red.id,
                "compute_price": "fixed",
                "fixed_price": 45.0,
            }
        )
        self._place_order(self.variant_red, 2)
        self._place_order(self.variant_blue, 7)
        self._place_order(self.variant_green, 1)
        wizard = self._open_template_wizard()
        values = wizard._get_report_values(wizard.ids)
        template_rows = [
            row
            for section in values["sections"]
            for row in section["rows"]
            if row["template"] == self.multi_template
        ]
        self.assertEqual(len(template_rows), 1)
        # Sum of all variants (red 2 + blue 7 + green 1).
        self.assertAlmostEqual(template_rows[0]["qty"], 10.0)
        # Divergent variant nests under the template row.
        inline = template_rows[0]["inline_exceptions"]
        self.assertEqual(len(inline), 1)
        self.assertEqual(inline[0]["product"], self.variant_red)
        self.assertAlmostEqual(inline[0]["qty"], 2.0)
        self.assertTrue(inline[0]["uom_label"])
        # Bottom "specials" block stays empty in template mode — no
        # double-display.
        self.assertEqual(values["variant_exceptions"], [])

    # ------------------------------------------------------------------
    # Invalid price filtering before aggregation
    # ------------------------------------------------------------------

    def test_invalid_priced_variant_excluded_from_qty_sum(self):
        """Variant with price above ``invalid_price_threshold`` is dropped.

        Its qty must NOT inflate the template row's qty — otherwise the
        customer would see a total that doesn't match what's printed.
        """
        # Variant-level override pushes red above the 99999 threshold.
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "0_product_variant",
                "product_id": self.variant_red.id,
                "compute_price": "fixed",
                "fixed_price": 999999.0,
            }
        )
        self._place_order(self.variant_red, 99)
        self._place_order(self.variant_blue, 4)
        self._place_order(self.variant_green, 6)
        wizard = self._open_template_wizard()
        values = wizard._get_report_values(wizard.ids)
        template_rows = [
            row
            for section in values["sections"]
            for row in section["rows"]
            if row["template"] == self.multi_template
        ]
        self.assertEqual(len(template_rows), 1)
        # Only blue (4) + green (6) — red is filtered.
        self.assertAlmostEqual(template_rows[0]["qty"], 10.0)
        # And red doesn't show up as exception either (invalid, not divergent).
        product_ids = [exc["product"].id for exc in values["variant_exceptions"]]
        self.assertNotIn(self.variant_red.id, product_ids)

    # ------------------------------------------------------------------
    # Markup (negative discount) protection on inline exceptions
    # ------------------------------------------------------------------

    def test_inline_exception_with_markup_hides_reference_and_discount(self):
        """Sub-row of a divergent variant with markup (negative
        ``total_discount``) mirrors the main row's protection in
        ``show_discounts`` mode: the reference column shows
        ``price_unit`` and the discount column renders as ``0,00``
        instead of exposing the negative percent to the customer.
        """
        # Allow markup on this test only — default config is 0%.
        self.env["ir.config_parameter"].sudo().set_param(
            "tr_commercial_policy.seller_markup_max_pct", "20.0"
        )
        # A condition line with a negative seller_discount on red
        # produces total_discount < 0 — markup. Created with
        # manager_user because writing seller_discount on a condition
        # line is gated by profile checks.
        self.condition.with_user(self.manager_user).line_ids = [
            (
                0,
                0,
                {
                    "applied_on": "product",
                    "product_id": self.variant_red.id,
                    "seller_discount": -10.0,
                    "extra_discount": 0.0,
                },
            )
        ]
        self._place_order(self.variant_red, 1)
        self._place_order(self.variant_blue, 1)
        self._place_order(self.variant_green, 1)
        wizard = self.env["tr.pricelist.report.wizard"].create(
            {
                "condition_id": self.condition.id,
                "layout": "historico",
                "history_grouping": "template",
                "discount_display": "show_discounts",
            }
        )
        values = wizard._get_report_values(wizard.ids)
        template_rows = [
            row
            for section in values["sections"]
            for row in section["rows"]
            if row["template"] == self.multi_template
        ]
        self.assertEqual(len(template_rows), 1)
        inline = template_rows[0]["inline_exceptions"]
        self.assertEqual(len(inline), 1)
        # Sanity check: the divergent variant's pricing dict carries
        # the negative total_discount we provoked.
        self.assertLess(inline[0]["total_discount"], 0)
        # And in the rendered HTML, the negative percent must not
        # appear — the QWeb conditional path falls back to "0,00".
        html, _type = self.env["ir.actions.report"]._render_qweb_html(
            "tr_pricelist_report.action_report_pricelist", wizard.ids
        )
        # Negative values can serialize as -10,00 (pt_BR) or -10.00.
        self.assertNotIn(b"-10,00", html)
        self.assertNotIn(b"-10.00", html)

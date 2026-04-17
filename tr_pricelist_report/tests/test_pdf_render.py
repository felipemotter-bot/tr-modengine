# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import PricelistReportTestCommon


class TestPdfRender(PricelistReportTestCommon):
    def test_report_renders_html(self):
        """Report renders to HTML without raising."""
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        html, _type = self.env["ir.actions.report"]._render_qweb_html(
            "tr_pricelist_report.action_report_pricelist", wizard.ids
        )
        self.assertIn(b"TABELA DE PRE", html)
        self.assertIn(self.customer.display_name.encode("utf-8"), html)

    def test_report_renders_with_show_discounts(self):
        """Template branches on ``discount_display`` without crashing."""
        wizard = self._open_wizard(
            category_ids=[self.categ_chemicals.id],
            discount_display="show_discounts",
        )
        html, _type = self.env["ir.actions.report"]._render_qweb_html(
            "tr_pricelist_report.action_report_pricelist", wizard.ids
        )
        self.assertIn(b"Pre\xc3\xa7o Ref.", html)
        self.assertIn(b"Desc. %", html)

    def test_report_renders_with_exceptions(self):
        """Exceptions section renders when variants diverge."""
        attr = self.env["product.attribute"].create(
            {"name": "Color", "create_variant": "always"}
        )
        val1 = self.env["product.attribute.value"].create(
            {"name": "White", "attribute_id": attr.id}
        )
        val2 = self.env["product.attribute.value"].create(
            {"name": "Red", "attribute_id": attr.id}
        )
        tmpl = self.env["product.template"].create(
            {
                "name": "Colored Template",
                "type": "consu",
                "list_price": 50.0,
                "categ_id": self.categ_chemicals.id,
                "attribute_line_ids": [
                    (
                        0,
                        0,
                        {
                            "attribute_id": attr.id,
                            "value_ids": [(6, 0, [val1.id, val2.id])],
                        },
                    ),
                ],
            }
        )
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "1_product",
                "product_tmpl_id": tmpl.id,
                "compute_price": "fixed",
                "fixed_price": 50.0,
            }
        )
        variant_white = tmpl.product_variant_ids.filtered(
            lambda v: val1
            in v.product_template_attribute_value_ids.mapped(
                "product_attribute_value_id"
            )
        )
        # Variant-specific price (variant exception)
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "0_product_variant",
                "product_id": variant_white.id,
                "compute_price": "fixed",
                "fixed_price": 45.0,
            }
        )
        # Quantity tier (qty exception)
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "0_product_variant",
                "product_id": variant_white.id,
                "compute_price": "fixed",
                "fixed_price": 40.0,
                "min_quantity": 100,
            }
        )
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        html, _type = self.env["ir.actions.report"]._render_qweb_html(
            "tr_pricelist_report.action_report_pricelist", wizard.ids
        )
        self.assertIn(b"Pre\xc3\xa7os Especiais por Variante", html)
        self.assertIn(b"Pre\xc3\xa7os por Quantidade", html)

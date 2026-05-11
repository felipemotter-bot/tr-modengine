# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import PricelistReportTestCommon


class TestPdfRender(PricelistReportTestCommon):
    """Cover the new semantic-class contract of the pricelist PDF template.

    Each assertion anchors on a ``pricelist-*`` class introduced in PR-B
    instead of brittle substring matches of inline styles. If the visual
    tuning changes (colors, paddings, widths) those tests don't break —
    but any structural regression (a missing section, column order swap,
    wrong layout branch) will.
    """

    def _render_html(self, wizard):
        html, _type = self.env["ir.actions.report"]._render_qweb_html(
            "tr_pricelist_report.action_report_pricelist", wizard.ids
        )
        return html

    def test_report_renders_header_with_title_and_client(self):
        """Header carries title + client/emission meta row.

        Document header now uses the shared ``.tr-doc-*`` classes from
        ``tr_report_style`` instead of the report-specific
        ``.pricelist-header*`` ones.
        """
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        html = self._render_html(wizard)
        self.assertIn(b'class="tr-doc-header"', html)
        self.assertIn(b'class="tr-doc-title"', html)
        self.assertIn("TABELA DE PREÇOS".encode("utf-8"), html)
        self.assertIn(b'class="tr-doc-meta"', html)
        self.assertIn(self.customer.display_name.encode("utf-8"), html)

    def test_report_loads_shared_style_kit(self):
        """Style kit marker proves ``tr_report_style.report_styles`` was called.

        If the consumer template forgets the ``t-call`` to the kit (or the kit
        module isn't installed) the marker class is missing and this test
        fails — even if the visual still looks right because of a leftover
        cached stylesheet.
        """
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        html = self._render_html(wizard)
        self.assertIn(b"tr-report-style-loaded", html)

    def test_report_does_not_render_codigo_column_header(self):
        """Column Código was absorbed by Descrição; header must not list it."""
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        html = self._render_html(wizard)
        # ``Código`` as a <th> header no longer exists in any section.
        self.assertNotIn(b"<th>C\xc3\xb3digo</th>", html)
        self.assertNotIn(b'<th style="width: 12%;">C\xc3\xb3digo</th>', html)

    def test_report_sections_use_semantic_titles(self):
        """Section titles now use the shared ``tr-section-title`` class.

        The pricelist-specific ``pricelist-table`` class still drives the
        product-table layout (variant exceptions, qty bands).
        """
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        html = self._render_html(wizard)
        self.assertIn(b'class="tr-section-title"', html)
        self.assertIn(b'class="pricelist-table"', html)

    def test_report_renders_with_show_discounts(self):
        """``show_discounts`` adds the Preço Ref. + Desc. % columns."""
        wizard = self._open_wizard(
            category_ids=[self.categ_chemicals.id],
            discount_display="show_discounts",
        )
        html = self._render_html(wizard)
        self.assertIn(b"Pre\xc3\xa7o Ref.", html)
        self.assertIn(b"Desc. %", html)

    def test_report_renders_with_exceptions(self):
        """Exception sections render with the same section-title class."""
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
        html = self._render_html(wizard)
        self.assertIn(b"Pre\xc3\xa7os Especiais por Variante", html)
        self.assertIn(b"Pre\xc3\xa7os por Quantidade", html)

    def test_history_layout_footer_note_present(self):
        """Historico footer note carries the full copy: window + date + returns."""
        # Place at least one confirmed sale so the history isn't empty.
        self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "pricelist_id": self.pricelist.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "product_uom_qty": 1,
                            "price_unit": 100.0,
                        },
                    ),
                ],
            }
        ).action_confirm()
        wizard = self._open_wizard(
            category_ids=[self.categ_chemicals.id], layout="historico"
        )
        html = self._render_html(wizard)
        self.assertIn(b'class="pricelist-history-note"', html)
        # Normalize whitespace before asserting so brittle indent/newlines
        # from QWeb output don't trip this test.
        note = b" ".join(html.split())
        self.assertIn("pedidos de venda confirmados".encode("utf-8"), note)
        self.assertIn("nos últimos".encode("utf-8"), note)
        self.assertIn("meses".encode("utf-8"), note)
        self.assertIn("Devoluções não são descontadas".encode("utf-8"), note)

    def test_history_footer_note_absent_on_other_layouts(self):
        """``geral`` doesn't carry the history note."""
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        html = self._render_html(wizard)
        self.assertNotIn(b'class="pricelist-history-note"', html)

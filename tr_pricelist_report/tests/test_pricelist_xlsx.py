# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import re
import zipfile
from io import BytesIO

from odoo import fields
from odoo.addons.mail.tests.common import MailCommon

from odoo.addons.tr_pricelist_report.report.pricelist_xlsx_report import (
    _safe_sheet_name,
)

from .common import PricelistReportTestCommon

# Every printable string in an xlsx sharedStrings.xml is wrapped in
# ``<t ...>value</t>`` (optional attrs handle ``xml:space="preserve"``).
_SHARED_STRING_RE = re.compile(r"<t[^>]*>([^<]*)</t>")


def _xlsx_strings(content):
    """Return the list of shared strings from rendered XLSX bytes.

    XLSX is a zip of XML parts. Headers/title/cell strings live in
    ``xl/sharedStrings.xml``. We don't need a full Excel parser to
    assert the content, just to peek into that part — keeps the test
    stack lean (no openpyxl/xlrd in the container image).
    """
    archive = zipfile.ZipFile(BytesIO(content))
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    payload = archive.read("xl/sharedStrings.xml").decode("utf-8")
    return _SHARED_STRING_RE.findall(payload)


class TestPricelistXlsx(PricelistReportTestCommon, MailCommon):
    """XLSX export — flat per-variant for both wizards."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer.email = "customer@example.com"

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

    def _open_history_wizard(self, **vals):
        defaults = {
            "condition_id": self.condition.id,
            "layout": "historico",
            "output_format": "xlsx",
        }
        defaults.update(vals)
        return self.env["tr.pricelist.report.wizard"].create(defaults)

    def _open_basic_wizard(self, **vals):
        defaults = {
            "pricelist_id": self.pricelist.id,
            "group_axis": "categoria",
            "output_format": "xlsx",
        }
        defaults.update(vals)
        return self.env["tr.pricelist.basic.wizard"].create(defaults)

    def _render_xlsx(self, report_xmlid, wizard_ids):
        report = self.env.ref(report_xmlid)
        content, ext = report._render_xlsx(report.report_name, wizard_ids, data={})
        self.assertEqual(ext, "xlsx")
        return content

    # ------------------------------------------------------------------
    # _strip_code_prefix and _format_uom_label live on the mixin
    # ------------------------------------------------------------------

    def test_strip_code_prefix_removes_bracket_when_default_code_set(self):
        """``[CODE] Name (variant)`` → ``Name (variant)``."""
        self.product_a.default_code = "ABC-001"
        wizard = self._open_history_wizard()
        stripped = wizard._strip_code_prefix(self.product_a)
        self.assertFalse(stripped.startswith("["))
        self.assertNotIn("ABC-001", stripped)

    def test_strip_code_prefix_noop_when_no_default_code(self):
        """No ``default_code`` → display_name returned untouched."""
        self.product_a.default_code = False
        wizard = self._open_history_wizard()
        self.assertEqual(
            wizard._strip_code_prefix(self.product_a),
            self.product_a.display_name,
        )

    # ------------------------------------------------------------------
    # _build_xlsx_rows_history — never consolidates per-template
    # ------------------------------------------------------------------

    def test_history_xlsx_rows_one_per_variant(self):
        """Two variants of the same template → two rows in the XLSX dataset.

        Even with ``history_grouping='template'`` set on the wizard
        (which would consolidate variants in the PDF), the XLSX must
        always emit one row per ``product.product``.
        """
        attr = self.env["product.attribute"].create(
            {"name": "Cor XLSX", "create_variant": "always"}
        )
        val_a = self.env["product.attribute.value"].create(
            {"name": "Azul", "attribute_id": attr.id}
        )
        val_b = self.env["product.attribute.value"].create(
            {"name": "Verde", "attribute_id": attr.id}
        )
        tmpl = self.env["product.template"].create(
            {
                "name": "Tinta XLSX",
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
                "product_tmpl_id": tmpl.id,
                "compute_price": "fixed",
                "fixed_price": 10.0,
            }
        )
        for variant in tmpl.product_variant_ids:
            self._place_confirmed_order(variant, 1)
        wizard = self._open_history_wizard(history_grouping="template")
        rows = wizard._build_xlsx_rows_history()
        product_names = [row["name"] for row in rows]
        # Each variant shows up; duplicates (template-level collapse) would
        # have produced a single row.
        self.assertEqual(sum(1 for name in product_names if "Tinta XLSX" in name), 2)

    def test_history_xlsx_rows_sorted_by_category_then_name(self):
        """Rows ordered by ``categ_id.complete_name`` then by name."""
        # Both fixture products are in categ_chemicals; product_b is in
        # categ_solvents (sub-category). The sort key includes the parent
        # path so solvents (Chemicals/Solvents) sort after chemicals
        # (Chemicals).
        self._place_confirmed_order(self.product_a, 1)
        self._place_confirmed_order(self.product_b, 1)
        wizard = self._open_history_wizard()
        rows = wizard._build_xlsx_rows_history()
        # The sort key on the resolver uses the complete_name + name. We
        # assert chemicals (parent) products come strictly before solvents
        # (child) products.
        positions = {}
        for idx, row in enumerate(rows):
            positions[row["name"]] = idx
        a_name = wizard._strip_code_prefix(self.product_a)
        b_name = wizard._strip_code_prefix(self.product_b)
        self.assertIn(a_name, positions)
        self.assertIn(b_name, positions)
        self.assertLess(positions[a_name], positions[b_name])

    def test_history_xlsx_rows_drop_invalid_prices(self):
        """Products above the invalid-price threshold are excluded."""
        self.env["ir.config_parameter"].sudo().set_param(
            "tr_pricelist_report.invalid_price_threshold", "150.0"
        )
        self._place_confirmed_order(self.product_a, 1)  # base 100 → kept
        self._place_confirmed_order(self.product_b, 1)  # base 200 → dropped
        wizard = self._open_history_wizard()
        rows = wizard._build_xlsx_rows_history()
        names = {row["name"] for row in rows}
        self.assertIn(self.product_a.display_name.split("] ", 1)[-1], names)
        self.assertFalse(
            any("200" in str(row["price_unit"]) for row in rows),
            "Product priced 200 should have been filtered out.",
        )

    # ------------------------------------------------------------------
    # End-to-end render — both wizards
    # ------------------------------------------------------------------

    def test_history_xlsx_renders_valid_workbook(self):
        """End-to-end render produces a valid XLSX with the expected layout."""
        self._place_confirmed_order(self.product_a, 1)
        self._place_confirmed_order(self.product_b, 1)
        wizard = self._open_history_wizard()
        content = self._render_xlsx(
            "tr_pricelist_report.action_report_pricelist_xlsx", wizard.ids
        )
        # Output is a real zipped xlsx — opening as zip must succeed.
        archive = zipfile.ZipFile(BytesIO(content))
        self.assertIn("xl/workbook.xml", archive.namelist())
        # Title and column headers live in sharedStrings.xml.
        strings = _xlsx_strings(content)
        title_strings = [s for s in strings if s.startswith("Price List")]
        self.assertTrue(title_strings, "Header title 'Price List ...' missing")
        self.assertIn(self.customer.name, title_strings[0])
        for header in (
            "Internal Reference",
            "Barcode",
            "Name",
            "UoM",
            "Price",
        ):
            self.assertIn(header, strings)

    def test_basic_xlsx_renders_valid_workbook(self):
        """Basic wizard XLSX render path."""
        wizard = self._open_basic_wizard()
        content = self._render_xlsx(
            "tr_pricelist_report.action_report_pricelist_basic_xlsx", wizard.ids
        )
        archive = zipfile.ZipFile(BytesIO(content))
        self.assertIn("xl/workbook.xml", archive.namelist())
        strings = _xlsx_strings(content)
        title_strings = [s for s in strings if s.startswith("Price List")]
        self.assertTrue(title_strings)
        self.assertIn(self.pricelist.name, title_strings[0])
        for header in (
            "Internal Reference",
            "Barcode",
            "Name",
            "UoM",
            "Price",
        ):
            self.assertIn(header, strings)

    def test_basic_xlsx_rows_drop_invalid_prices(self):
        """Basic XLSX honors the same ``_is_valid_price`` threshold as PDF."""
        self.env["ir.config_parameter"].sudo().set_param(
            "tr_pricelist_report.invalid_price_threshold", "150.0"
        )
        wizard = self._open_basic_wizard()
        rows = wizard._build_xlsx_rows_basic()
        # product_a base 100 → kept; product_b base 200 → dropped.
        codes = {row["default_code"] for row in rows if row["default_code"]}
        if self.product_a.default_code:
            self.assertIn(self.product_a.default_code, codes)
        if self.product_b.default_code:
            self.assertNotIn(self.product_b.default_code, codes)

    def test_basic_xlsx_simulated_return_applies(self):
        """``simulated_contractual_return`` propagates to the price column."""
        wizard = self._open_basic_wizard(simulated_contractual_return=10.0)
        rows = wizard._build_xlsx_rows_basic()
        prices_by_code = {
            row["default_code"]: row["price_unit"]
            for row in rows
            if row["default_code"]
        }
        # product_a base is 100 in the fixture; with 10% simulated return → 90.
        if self.product_a.default_code:
            self.assertAlmostEqual(prices_by_code[self.product_a.default_code], 90.0)

    # ------------------------------------------------------------------
    # action_generate ramification
    # ------------------------------------------------------------------

    def test_action_generate_xlsx_returns_xlsx_report_action(self):
        """With cliente: ``output_format='xlsx'`` returns the XLSX action."""
        self._place_confirmed_order(self.product_a, 1)
        wizard = self._open_history_wizard()
        action = wizard.action_generate()
        self.assertEqual(action["type"], "ir.actions.report")
        self.assertEqual(
            action["report_name"], "tr_pricelist_report.report_pricelist_xlsx"
        )

    def test_action_generate_pdf_unchanged(self):
        """``output_format='pdf'`` keeps the existing PDF action wiring."""
        self._place_confirmed_order(self.product_a, 1)
        wizard = self._open_history_wizard(output_format="pdf")
        action = wizard.action_generate()
        self.assertEqual(
            action["report_name"],
            "tr_pricelist_report.report_pricelist_document",
        )

    def test_action_generate_xlsx_forces_historico_layout(self):
        """XLSX + layout='geral' is coerced back to 'historico' before generate.

        The form view already hides the layout field when XLSX is
        selected, but the model-level guard protects the engine from a
        request crafted via API or older clients.
        """
        self._place_confirmed_order(self.product_a, 1)
        wizard = self._open_history_wizard(layout="geral")
        wizard.action_generate()
        self.assertEqual(wizard.layout, "historico")

    def test_basic_action_generate_xlsx_returns_xlsx_action(self):
        """Basic wizard XLSX path."""
        wizard = self._open_basic_wizard()
        action = wizard.action_generate()
        self.assertEqual(
            action["report_name"],
            "tr_pricelist_report.report_pricelist_basic_xlsx",
        )

    def test_basic_action_generate_pdf_unchanged(self):
        """Basic wizard PDF path is the existing default."""
        wizard = self._open_basic_wizard(output_format="pdf")
        action = wizard.action_generate()
        self.assertEqual(
            action["report_name"],
            "tr_pricelist_report.report_pricelist_basic_document",
        )

    # ------------------------------------------------------------------
    # Send by email — XLSX attachment
    # ------------------------------------------------------------------

    def test_send_by_email_xlsx_creates_xlsx_attachment(self):
        """Composer is opened with an XLSX attachment when output=xlsx."""
        self._place_confirmed_order(self.product_a, 1)
        wizard = self._open_history_wizard(send_by_email=True)
        action = wizard.action_generate()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "mail.compose.message")
        attachment_ids = action["context"]["default_attachment_ids"]
        self.assertEqual(len(attachment_ids), 1)
        attachment = self.env["ir.attachment"].browse(attachment_ids[0])
        self.assertEqual(
            attachment.mimetype,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertTrue(attachment.name.endswith(".xlsx"))
        # Filename includes today's date (YYYY-MM-DD) so previous
        # exports don't get silently overwritten in a download folder.
        self.assertIn(fields.Date.today().strftime("%Y-%m-%d"), attachment.name)
        # Same temporary-attachment guarantees as the PDF path.
        self.assertEqual(attachment.res_model, "mail.compose.message")
        self.assertEqual(attachment.res_id, 0)

    def test_send_by_email_pdf_attachment_filename_unchanged(self):
        """PDF attachment name is not regressed by the XLSX feature.

        Codex 2026-05-07: original PDF filename was
        ``Price List - <partner>.pdf`` and must not gain a date suffix
        or change to Portuguese — that's a regression to the PDF-only
        flow that was working in production.
        """
        self._place_confirmed_order(self.product_a, 1)
        wizard = self._open_history_wizard(output_format="pdf", send_by_email=True)
        action = wizard.action_generate()
        attachment = self.env["ir.attachment"].browse(
            action["context"]["default_attachment_ids"][0]
        )
        self.assertEqual(attachment.mimetype, "application/pdf")
        self.assertTrue(attachment.name.startswith("Price List - "))
        self.assertTrue(attachment.name.endswith(".pdf"))

    # ------------------------------------------------------------------
    # Safe sheet name helper
    # ------------------------------------------------------------------

    def test_safe_sheet_name_strips_invalid_characters(self):
        """Excel-forbidden characters are replaced; whitespace is trimmed."""
        # Excel rejects: [ ] : * ? / \\ . They get squashed to a space
        # and then the result is stripped + capped at 31 chars.
        self.assertEqual(_safe_sheet_name("Cliente / Filial"), "Cliente   Filial")
        self.assertEqual(_safe_sheet_name("[ACME] Co: subdiv*?"), "ACME  Co  subdiv")

    def test_safe_sheet_name_falls_back_when_empty(self):
        """Empty or all-invalid names fall back to ``Pricelist``."""
        self.assertEqual(_safe_sheet_name(""), "Pricelist")
        self.assertEqual(_safe_sheet_name("///"), "Pricelist")
        self.assertEqual(_safe_sheet_name(None), "Pricelist")

    def test_safe_sheet_name_caps_at_31_chars(self):
        """Excel limit is 31 characters, longer names are truncated."""
        long = "x" * 50
        self.assertEqual(len(_safe_sheet_name(long)), 31)

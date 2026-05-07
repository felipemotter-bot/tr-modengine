# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import re

from odoo import _, fields, models

# Excel forbids these characters in worksheet names; pre-compiled because
# every sheet write goes through ``_safe_sheet_name``.
_INVALID_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


def _safe_sheet_name(name):
    """Sanitize a string for use as an Excel worksheet name.

    Excel forbids ``[]:*?/\\`` and caps the name at 31 chars. Empty
    names (whitespace-only or empty after stripping) fall back to
    ``"Pricelist"`` so the workbook always has a usable sheet.
    """
    cleaned = _INVALID_SHEET_CHARS.sub(" ", name or "").strip()
    if not cleaned:
        cleaned = "Pricelist"
    return cleaned[:31]


def _write_sheet(workbook, sheet_name, title, rows):
    """Render a single sheet with the shared XLSX layout.

    Layout:
    - Row 1: title + metadata (merged across all 5 columns)
    - Row 2: blank
    - Row 3: column headers (bold + filled)
    - Row 4+: data, with AutoFilter on the header+data range.
    """
    sheet = workbook.add_worksheet(_safe_sheet_name(sheet_name))
    title_format = workbook.add_format(
        {"bold": True, "font_size": 12, "align": "left", "valign": "vcenter"}
    )
    header_format = workbook.add_format(
        {
            "bold": True,
            "bg_color": "#D9D9D9",
            "border": 1,
            "align": "center",
            "valign": "vcenter",
        }
    )
    price_format = workbook.add_format({"num_format": "#,##0.0000"})

    sheet.merge_range(0, 0, 0, 4, title, title_format)

    headers = [
        _("Internal Reference"),
        _("Barcode"),
        _("Name"),
        _("UoM"),
        _("Price"),
    ]
    for col_idx, label in enumerate(headers):
        sheet.write(2, col_idx, label, header_format)

    for offset, row in enumerate(rows):
        excel_row = 3 + offset
        sheet.write_string(excel_row, 0, row["default_code"] or "")
        sheet.write_string(excel_row, 1, row["barcode"] or "")
        sheet.write_string(excel_row, 2, row["name"] or "")
        sheet.write_string(excel_row, 3, row["uom"] or "")
        sheet.write_number(excel_row, 4, row["price_unit"] or 0.0, price_format)

    # Even with no rows, attach the autofilter to the header row alone
    # so the filter dropdowns still appear.
    last_row = max(2, 2 + len(rows))
    sheet.autofilter(2, 0, last_row, 4)

    sheet.set_column(0, 0, 18)
    sheet.set_column(1, 1, 18)
    sheet.set_column(2, 2, 50)
    sheet.set_column(3, 3, 8)
    sheet.set_column(4, 4, 14)


class PricelistXlsxReport(models.AbstractModel):
    """XLSX flavor of the customer-history pricelist (with-customer wizard).

    100% per variant: ignores ``history_grouping`` (no template
    consolidation), ignores band/qty exceptions. Each row is one
    ``product.product`` purchased in the history window, priced at
    qty=1 through ``_compute_pricing`` (same engine as the PDF).
    """

    _name = "report.tr_pricelist_report.report_pricelist_xlsx"
    _inherit = "report.report_xlsx.abstract"
    _description = "Pricelist Report XLSX"

    def generate_xlsx_report(self, workbook, data, wizards):
        for wizard in wizards:
            rows = wizard._build_xlsx_rows_history()
            partner = wizard.condition_id.partner_id
            title = _(
                "Price List — %(partner)s — %(condition)s — %(date)s",
                partner=partner.display_name or "",
                condition=wizard.condition_id.display_name or "",
                date=fields.Date.today().strftime("%Y-%m-%d"),
            )
            _write_sheet(workbook, partner.name or "", title, rows)


class PricelistBasicXlsxReport(models.AbstractModel):
    """XLSX flavor of the catalog-only pricelist (basic wizard, no customer)."""

    _name = "report.tr_pricelist_report.report_pricelist_basic_xlsx"
    _inherit = "report.report_xlsx.abstract"
    _description = "Basic Pricelist Report XLSX"

    def generate_xlsx_report(self, workbook, data, wizards):
        for wizard in wizards:
            rows = wizard._build_xlsx_rows_basic()
            date_label = (wizard.date or fields.Date.today()).strftime("%Y-%m-%d")
            title = _(
                "Price List — %(pricelist)s — %(company)s — %(date)s",
                pricelist=wizard.pricelist_id.name or "",
                company=wizard.company_id.name or "",
                date=date_label,
            )
            _write_sheet(workbook, wizard.pricelist_id.name or "", title, rows)

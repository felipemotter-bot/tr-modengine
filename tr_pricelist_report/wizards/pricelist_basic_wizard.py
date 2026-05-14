# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PricelistBasicWizard(models.TransientModel):
    """Basic Pricelist wizard for external sales reps.

    Prints a pricelist without applying any ``partner.commercial.condition``
    — the target user is a sales rep who doesn't have a customer in hand
    yet and just wants the raw pricelist values, optionally with a
    simulated contractual-return discount on top. Shares the section
    pipeline with the General Pricelist via
    ``tr.pricelist.report.section.builder``.
    """

    _name = "tr.pricelist.basic.wizard"
    _inherit = ["tr.pricelist.report.section.builder"]
    _description = "Basic Pricelist Wizard"
    _check_company_auto = True

    pricelist_id = fields.Many2one(
        "product.pricelist",
        string="Pricelist",
        required=True,
        check_company=True,
        domain="[('company_id', 'in', [False, company_id])]",
        default=lambda self: self._default_pricelist_id(),
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )
    group_axis = fields.Selection(
        [("marca", "By Brand"), ("categoria", "By Category")],
        string="Grouping Axis",
        required=True,
        default="marca",
    )
    category_ids = fields.Many2many(
        "product.category",
        string="Categories",
        help="Pick one or more categories to narrow the scope. Sub-categories "
        "are included automatically. Leave empty to print every sellable "
        "product in the scope of the selected company.",
    )
    simulated_contractual_return = fields.Float(
        string="Simulated Contractual Return (%)",
        default=0.0,
        help="Percent discount simulated on top of the raw pricelist price. "
        "Exists only to preview the effect of a contractual return — not "
        "tied to any commercial condition.",
    )
    date = fields.Date(
        string="Reference Date",
        default=fields.Date.context_today,
        help="Date used to resolve pricelist tiers. Defaults to today.",
    )
    output_format = fields.Selection(
        [("pdf", "PDF"), ("xlsx", "XLSX")],
        required=True,
        default="pdf",
        help=(
            "PDF prints the formatted catalog with section grouping. "
            "XLSX exports a flat per-variant spreadsheet (code, barcode, "
            "name, UoM, price) intended for filtering and search."
        ),
    )

    # ------------------------------------------------------------------
    # Defaults
    # ------------------------------------------------------------------

    def _default_pricelist_id(self):
        """Return the same pricelist a new customer would inherit by default.

        Mirrors the Odoo core behavior: when no explicit pricelist is set
        on a partner, the system picks the first ``product.pricelist``
        sorted by ``sequence`` ascending (the one at the top of the
        Pricelists tree view) within the current company scope. We do the
        same here so reps opening the basic wizard already see a sane
        default — they can still pick a different one before generating.

        An empty recordset's ``.id`` is already ``False``, so we don't
        need an explicit ``or False`` fallback.
        """
        company = self.env.company
        return (
            self.env["product.pricelist"]
            .search(
                [("company_id", "in", (company.id, False))],
                order="sequence, id",
                limit=1,
            )
            .id
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_generate(self):
        self.ensure_one()
        report_xmlid = (
            "tr_pricelist_report.action_report_pricelist_basic_xlsx"
            if self.output_format == "xlsx"
            else "tr_pricelist_report.action_report_pricelist_basic"
        )
        return self.env.ref(report_xmlid).report_action(self, config=False)

    # ------------------------------------------------------------------
    # XLSX flat row builder (always per-variant)
    # ------------------------------------------------------------------

    def _build_xlsx_rows_basic(self):
        """Flat per-variant rows for the XLSX export of the basic catalog.

        Reuses ``_resolve_products`` and ``_resolve_basic_pricing`` so
        the pricing engine matches the PDF. Sort order: category
        complete_name → name (with the ``[code]`` prefix stripped).
        """
        self.ensure_one()
        products = self._resolve_products(
            category_ids=self.category_ids,
            company_id=self.company_id.id,
        )
        rows = []
        for product in products:
            pricing = self._resolve_basic_pricing(product)
            if not self._is_valid_price(pricing["price_unit"]):
                continue
            name = self._strip_code_prefix(product)
            rows.append(
                {
                    "default_code": product.default_code or "",
                    "barcode": product.barcode or "",
                    "name": name,
                    "uom": self._format_uom_label(product.uom_id),
                    "price_unit": pricing["price_unit"],
                    "_sort_key": (
                        product.categ_id.complete_name or "",
                        name,
                    ),
                }
            )
        rows.sort(key=lambda row: row["_sort_key"])
        for row in rows:
            row.pop("_sort_key", None)
        return rows

    # ------------------------------------------------------------------
    # Pricing resolver (callback passed to the section builder)
    # ------------------------------------------------------------------

    def _resolve_basic_pricing(self, product):
        """Return the shared pricing dict for ``product``.

        Uses the canonical ``_get_product_price(partner=False)`` path,
        which respects ``base_pricelist_id`` chains and pricelist tiers
        without applying any partner-specific rule. The optional
        ``simulated_contractual_return`` percent is applied as a flat
        multiplier on top, and reported on its own dict key so the
        template can render it as "retorno simulado" instead of masking
        it as a seller discount.
        """
        self.ensure_one()
        base = self.pricelist_id._get_product_price(
            product, 1.0, partner=False, date=self.date or fields.Date.today()
        )
        simulated = self.simulated_contractual_return or 0.0
        return {
            "product": product,
            "base": base,
            "reference": base,
            "seller_discount": 0.0,
            "total_discount": 0.0,
            "simulated_contractual_return": simulated,
            "price_unit": base * (1.0 - simulated / 100.0),
        }

    # ------------------------------------------------------------------
    # Entry point used by the report engine
    # ------------------------------------------------------------------

    @api.model
    def _get_report_values(self, docids, data=None):
        wizard = self.browse(docids[0])
        products = wizard._resolve_products(
            category_ids=wizard.category_ids,
            company_id=wizard.company_id.id,
        )
        sections = wizard._build_sections(
            products, wizard._resolve_basic_pricing, group_axis=wizard.group_axis
        )
        variant_exceptions = [
            exc for section in sections for exc in section["variant_exceptions"]
        ]
        has_body = any(section["rows"] for section in sections)
        if not has_body and not variant_exceptions:
            raise UserError(
                _(
                    "No products with valid prices to generate the pricelist. "
                    "Check the pricelist configuration or the invalid price "
                    "threshold setting."
                )
            )
        return {
            "doc_ids": docids,
            "doc_model": "tr.pricelist.basic.wizard",
            "docs": wizard,
            "wizard": wizard,
            "company": wizard.company_id,
            "pricelist": wizard.pricelist_id,
            "date_issued": wizard.date or fields.Date.today(),
            "simulated_contractual_return": wizard.simulated_contractual_return,
            "sections": sections,
            "variant_exceptions": variant_exceptions,
        }

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from collections import defaultdict
from functools import partial

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare, float_round

from odoo.addons.tr_commercial_policy.models.policy_utils import (
    calc_price_unit,
    calc_reference_price,
    get_policy_rates,
)


class PricelistReportWizard(models.TransientModel):
    _name = "tr.pricelist.report.wizard"
    _inherit = ["tr.pricelist.report.section.builder"]
    _description = "Pricelist Report Wizard"

    condition_id = fields.Many2one(
        "partner.commercial.condition",
        string="Commercial Condition",
        required=True,
        default=lambda self: self._default_condition_id(),
    )
    layout = fields.Selection(
        [
            ("geral", "General Pricelist"),
            ("historico", "Customer History"),
        ],
        required=True,
        default="historico",
    )
    group_axis = fields.Selection(
        [("marca", "By Brand"), ("categoria", "By Category")],
        required=True,
        default="marca",
        help="Grouping axis for the General Pricelist layout.",
    )
    history_grouping = fields.Selection(
        [
            ("variante", "By Variant"),
            ("template", "By Product Template"),
        ],
        required=True,
        default="template",
        help="Detail level for the Customer History layout. By Variant prints "
        "one row per purchased SKU; By Product Template collapses variants "
        "with the same price into a single template row, with divergent "
        "variants listed below as exceptions.",
    )
    history_months_back = fields.Integer(
        string="History Window (months)",
        default=lambda self: self._default_history_months_back(),
        help=(
            "Sales from the last N months are considered for this print. "
            "The default comes from Settings."
        ),
    )
    category_ids = fields.Many2many(
        "product.category",
        string="Categories",
        help="Pick one or more product categories. Sub-categories are included "
        "automatically. Always optional — leave empty to print every "
        "sellable product.",
        # Always optional now that por_categoria is gone. The wizard prints
        # every active + sale_ok product when empty, filtered to selected
        # categories (and their descendants) when populated.
    )
    discount_display = fields.Selection(
        [
            ("show_discounts", "Show reference and discounts"),
            ("net_price", "Net price only"),
        ],
        string="Price Display",
        required=True,
        default=lambda self: self._default_discount_display(),
    )
    send_by_email = fields.Boolean(
        string="Send by email",
        default=False,
        help=(
            "When checked, the generated file is attached to a mail composer "
            "pre-filled for the partner's email; you can review and edit "
            "before sending. When unchecked, the file downloads directly."
        ),
    )
    output_format = fields.Selection(
        [("pdf", "PDF"), ("xlsx", "XLSX")],
        required=True,
        default="pdf",
        help=(
            "PDF prints the formatted pricelist with consolidation and "
            "section grouping. XLSX exports a flat per-variant spreadsheet "
            "(code, barcode, name, UoM, price) intended for filtering and "
            "search; XLSX is only available with the customer-history "
            "layout."
        ),
    )

    # ------------------------------------------------------------------
    # Defaults
    # ------------------------------------------------------------------

    def _default_condition_id(self):
        context = self.env.context
        active_model = context.get("active_model")
        active_id = context.get("active_id")
        if active_model == "partner.commercial.condition" and active_id:
            return active_id
        if active_model == "res.partner" and active_id:
            partner = self.env["res.partner"].browse(active_id)
            return partner.effective_condition_id.id or False
        return False

    def _default_discount_display(self):
        condition_id = self._default_condition_id()
        if condition_id:
            condition = self.env["partner.commercial.condition"].browse(condition_id)
            return condition.discount_display or "net_price"
        return "net_price"

    def _default_history_months_back(self):
        return self._get_history_months_back()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_generate(self):
        self.ensure_one()
        # XLSX is only meaningful in the customer-history layout: the
        # General PDF consolidates by template and the spreadsheet must be
        # 100% per-variant. Force it defensively even though the form view
        # already hides the layout/grouping fields when XLSX is selected.
        if self.output_format == "xlsx" and self.layout != "historico":
            self.layout = "historico"
        if self.layout == "historico" and not self._resolve_history_quantities():
            raise UserError(
                _(
                    "No sales history found for this customer in the "
                    "configured period."
                )
            )
        if self.send_by_email:
            return self._open_mail_composer()
        # ``config=False`` skips the "configure external layout" wizard that
        # Odoo prompts admins with on first use (returns ir.actions.act_window
        # instead of the report). Pricelist printing shouldn't derail on the
        # layout configurator — the user already asked for a report.
        report_xmlid = (
            "tr_pricelist_report.action_report_pricelist_xlsx"
            if self.output_format == "xlsx"
            else "tr_pricelist_report.action_report_pricelist"
        )
        return self.env.ref(report_xmlid).report_action(self, config=False)

    def _open_mail_composer(self):
        """Render the report, open mail composer with a transient attachment.

        The email "belongs" to the ``partner.commercial.condition`` so
        when actually sent it gets logged in the condition's chatter
        (persistent history). The report itself is rendered by an
        ``ir.actions.report`` whose ``model`` is the transient wizard —
        ``report_template_ids`` on the mail template doesn't fit that
        shape, so we render the bytes here and hand them off to the
        composer as a **temporary** attachment (``res_model
        ='mail.compose.message'``, ``res_id=0``). When
        ``mail.compose.message._action_send_mail`` actually posts the
        message, ``message_post`` re-parents the attachment to the
        condition. If the user cancels the composer the attachment
        stays temporary and Odoo's built-in garbage collector reclaims
        it later — nothing pollutes the condition's attachment tree.
        """
        self.ensure_one()
        condition = self.condition_id
        partner = condition.partner_id
        if self.output_format == "xlsx":
            report = self.env.ref("tr_pricelist_report.action_report_pricelist_xlsx")
            content, _ext = report._render_xlsx(report.report_name, self.ids, data={})
            attachment_name = "Price List - %s - %s.xlsx" % (
                partner.name or "",
                fields.Date.today().strftime("%Y-%m-%d"),
            )
            mimetype = (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            report = self.env.ref("tr_pricelist_report.action_report_pricelist")
            content, _ext = report._render_qweb_pdf(report.report_name, self.ids)
            attachment_name = "Price List - %s.pdf" % (partner.name or "")
            mimetype = "application/pdf"
        attachment = self.env["ir.attachment"].create(
            {
                "name": attachment_name,
                "type": "binary",
                "datas": base64.b64encode(content),
                "res_model": "mail.compose.message",
                "res_id": 0,
                "mimetype": mimetype,
            }
        )
        template = self.env.ref(
            "tr_pricelist_report.email_template_pricelist",
            raise_if_not_found=False,
        )
        compose_ctx = {
            "default_model": "partner.commercial.condition",
            "default_res_id": condition.id,
            "default_composition_mode": "comment",
            "default_attachment_ids": [attachment.id],
        }
        if template:
            compose_ctx["default_use_template"] = True
            compose_ctx["default_template_id"] = template.id
        return {
            "type": "ir.actions.act_window",
            "name": _("Send Price List"),
            "res_model": "mail.compose.message",
            "view_mode": "form",
            "target": "new",
            "context": compose_ctx,
        }

    # ------------------------------------------------------------------
    # Report values
    # ------------------------------------------------------------------

    def _expand_categories(self, category_ids=None):
        """Override that defaults to ``self.category_ids``.

        The signature is kept compatible with the mixin (``category_ids``
        arg) so that calls coming from the mixin's own
        ``_resolve_products`` don't trip on the override, while the
        callers that used the pre-mixin ``_expand_categories()`` shape
        keep working.
        """
        if category_ids is None:
            category_ids = self.category_ids
        return super()._expand_categories(category_ids)

    def _resolve_products(self, category_ids=None, company_id=False):
        """Override that defaults to ``self.category_ids``.

        Multi-company scoping on the General layout is enforced by the
        ``partner.commercial.condition`` record rules and by the
        condition itself pinning the pricelist — so ``company_id`` is
        not pushed down here by default (callers can still override).
        """
        if category_ids is None:
            category_ids = self.category_ids
        return super()._resolve_products(
            category_ids=category_ids, company_id=company_id
        )

    # ------------------------------------------------------------------
    # Grouping resolvers (shared between layouts)
    # ------------------------------------------------------------------

    def _get_history_months_back(self):
        value = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("tr_pricelist_report.history_months_back", "6")
        )
        try:
            return int(value)
        except (TypeError, ValueError):
            return 6

    def _get_fiscal_position(self, partner, company):
        """Resolve the fiscal position the same way ``sale.order`` does.

        ``sale.order._compute_fiscal_position_id`` (`sale_order.py:350`) uses
        ``account.fiscal.position._get_fiscal_position(partner, shipping)``
        with the default delivery partner — not
        ``partner.property_account_position_id``, which can diverge when the
        FP depends on the shipping address. Mirroring that logic keeps the
        printed base_price in parity with what the order would resolve.
        """
        shipping_id = partner.address_get(["delivery"]).get("delivery")
        shipping = (
            self.env["res.partner"].browse(shipping_id) if shipping_id else partner
        )
        return (
            self.env["account.fiscal.position"]
            .with_company(company)
            ._get_fiscal_position(partner, shipping)
        )

    def _compute_base_price(self, product, qty=1.0):
        """Return base price for ``product`` under this condition.

        Mirrors the same chain used by ``sale.order.line._compute_base_price``
        (``pricelist._get_product_price`` →
        ``product._get_tax_included_unit_price``) so the printed price
        matches what an order/invoice would compute.

        ``qty`` is forwarded to the pricelist lookup so quantity tiers
        (``pricelist_item.min_quantity > 0``) return the tier price.
        """
        condition = self.condition_id
        pricelist = condition.pricelist_id
        partner = condition.partner_id
        company = condition.company_id or self.env.company
        today = fields.Date.today()
        price = pricelist.with_company(company)._get_product_price(
            product,
            qty,
            partner=partner,
            uom=product.uom_id,
            date=today,
        )
        return product._get_tax_included_unit_price(
            company,
            pricelist.currency_id,
            today,
            "sale",
            fiscal_position=self._get_fiscal_position(partner, company),
            product_price_unit=price,
            product_currency=pricelist.currency_id,
        )

    # ``skip_adjustment_factor`` / ``block_discounts`` are fields added by the
    # optional ``trento_commercial_policy_transition`` module. Read defensively
    # via these helpers so the report works whether that module is installed
    # or not (and so tests can mock them without patching ``getattr``).

    def _pricelist_skip_adjustment(self, pricelist):
        return getattr(pricelist, "skip_adjustment_factor", False)

    def _pricelist_blocks_discounts(self, pricelist):
        return getattr(pricelist, "block_discounts", False)

    def _compute_pricing(self, product, rates, qty=1.0, include_inline_bands=True):
        """Return full pricing dict for a product.

        ``qty`` is forwarded to ``_compute_base_price`` AND to
        ``_resolve_discount_for_product`` so both the pricelist tier
        AND the condition.line band that match that qty are honored.
        For the main row, ``qty`` defaults to 1.0 (unit-level pricing).
        ``inline_bands`` re-prices each band using its own qty;
        ``include_inline_bands=False`` skips that lookup for callers
        (XLSX) that only need the qty=1 row and don't render bands.
        """
        condition = self.condition_id
        base = self._compute_base_price(product, qty=qty)
        cr = condition.contractual_return or 0.0
        tax_rate, freight_rate, admin_rate = rates
        pricelist = condition.pricelist_id
        if self._pricelist_skip_adjustment(pricelist):
            reference = base
        else:
            reference = calc_reference_price(
                base, cr, tax_rate, freight_rate, admin_rate
            )
        if self._pricelist_blocks_discounts(pricelist):
            seller = 0.0
            extra = 0.0
            inline_bands = []
        else:
            seller, extra, _level = condition._resolve_discount_for_product(
                product, qty=qty, uom=product.uom_id
            )
            if include_inline_bands:
                # Each band re-resolves base (pricelist tier may shift
                # at band qty) AND its own seller/extra (condition.line
                # band).
                inline_bands = self._resolve_inline_bands(product, condition, rates)
            else:
                inline_bands = []
        price_unit = calc_price_unit(reference, seller, extra)
        return {
            "product": product,
            "base": base,
            "reference": reference,
            "seller_discount": seller,
            "total_discount": (seller or 0.0) + (extra or 0.0),
            "simulated_contractual_return": 0.0,
            "price_unit": price_unit,
            "inline_bands": inline_bands,
        }

    def _resolve_inline_bands(self, product, condition, rates):
        """Return per-band pricing rows for the product.

        For each band on the matching ``condition.line``, re-prices
        the product at the band's qty so the sub-row reflects:
        - the pricelist's ``min_quantity`` tier active at that qty
          (e.g. fixed_price=80 if qty>=50 in the pricelist)
        - the band's seller/extra discount on top
        Without this, sub-rows would mis-state the price when both
        the pricelist and the condition.line have qty thresholds.
        """
        line = condition.line_ids.filtered(lambda cl: cl.product_id == product)
        if not line:
            line = condition.line_ids.filtered(
                lambda cl: cl.product_tmpl_id == product.product_tmpl_id
                and cl.applied_on == "product_template"
            )
        if not line or not line[0].band_ids:
            return []
        cr = condition.contractual_return or 0.0
        tax_rate, freight_rate, admin_rate = rates
        pricelist = condition.pricelist_id
        rows = []
        for band in line[0].band_ids.sorted("qty_min"):
            # Convert band.qty_min to the product's UoM so pricelist
            # tier matching is consistent.
            band_qty = band._normalize_qty_min(product.uom_id) or band.qty_min
            band_base = self._compute_base_price(product, qty=band_qty)
            if self._pricelist_skip_adjustment(pricelist):
                band_reference = band_base
            else:
                band_reference = calc_reference_price(
                    band_base, cr, tax_rate, freight_rate, admin_rate
                )
            band_seller = band.seller_discount or 0.0
            band_extra = band.extra_discount or 0.0
            rows.append(
                {
                    "qty_min": band.qty_min or 0.0,
                    "qty_uom_label": (band.qty_uom_id.name or "").replace(
                        "CAIXA", "CX"
                    ),
                    "seller_discount": band_seller,
                    "extra_discount": band_extra,
                    "total_discount": band_seller + band_extra,
                    "reference": band_reference,
                    "price_unit": calc_price_unit(
                        band_reference, band_seller, band_extra
                    ),
                }
            )
        return rows

    def _resolve_qty_exceptions(self, products, rates):
        """Return tier-quantity exceptions for the scope products.

        Any ``pricelist_item`` on the condition's pricelist with
        ``min_quantity > 0`` that matches a product or template in
        ``products`` is returned as an exception row with the **policy
        price** for that quantity — i.e., the pricelist item's
        ``fixed_price`` passed through ``_get_tax_included_unit_price``
        and the policy formula (contractual return + seller discount).
        Printing ``item.fixed_price`` raw would expose a pricelist base
        value while every other row in the report shows the final
        commercial price.

        For template-level tiers (``applied_on='1_product'``), we price
        each active variant of the template at the tier quantity. If all
        variants resolve to the same price (same pricelist tier + same
        condition discount) we collapse into a single row targeting the
        template. If the prices diverge — which happens when the
        condition has variant-specific ``line_ids`` coexisting with the
        template tier — we emit one row per variant so the customer sees
        the actual price they'll pay for each.
        """
        pricelist = self.condition_id.pricelist_id
        templates = products.mapped("product_tmpl_id")
        items = pricelist.item_ids.filtered(
            lambda item: item.min_quantity
            and float_compare(item.min_quantity, 0.0, precision_digits=2) > 0
            and (
                (item.applied_on == "1_product" and item.product_tmpl_id in templates)
                or (
                    item.applied_on == "0_product_variant"
                    and item.product_id in products
                )
            )
        )
        precision = self.env["decimal.precision"].precision_get("Product Price")
        rows = []
        for item in items:
            if item.applied_on == "0_product_variant":
                pricing = self._compute_pricing(
                    item.product_id, rates, qty=item.min_quantity
                )
                if not self._is_valid_price(pricing["price_unit"]):
                    continue
                rows.append(
                    {
                        "target": item.product_id,
                        "min_qty": item.min_quantity,
                        "pricing": pricing,
                    }
                )
                continue
            # Template tier: price every active variant at the tier qty.
            variants = item.product_tmpl_id.product_variant_ids.filtered("active")
            variant_pricings = [
                (v, self._compute_pricing(v, rates, qty=item.min_quantity))
                for v in variants
            ]
            variant_pricings = [
                (v, p)
                for (v, p) in variant_pricings
                if self._is_valid_price(p["price_unit"])
            ]
            if not variant_pricings:
                continue
            # Same four-field key used by ``_consolidate_templates`` so the
            # template-collapsed row is truthful in both display modes.
            unique_prices = {
                (
                    float_round(p["price_unit"], precision_digits=precision),
                    float_round(p["reference"], precision_digits=precision),
                    float_round(p["seller_discount"], precision_digits=2),
                    float_round(p["simulated_contractual_return"], precision_digits=2),
                )
                for _v, p in variant_pricings
            }
            if len(unique_prices) <= 1:
                # Same price across all variants → single row at template level.
                rows.append(
                    {
                        "target": item.product_tmpl_id,
                        "min_qty": item.min_quantity,
                        "pricing": variant_pricings[0][1],
                    }
                )
            else:
                # Divergent prices (template tier + per-variant discount) →
                # one row per variant so the customer sees each actual price.
                for variant, pricing in variant_pricings:
                    rows.append(
                        {
                            "target": variant,
                            "min_qty": item.min_quantity,
                            "pricing": pricing,
                        }
                    )
        rows.sort(key=lambda row: (row["target"].display_name, row["min_qty"]))
        return rows

    # ------------------------------------------------------------------
    # Customer-history layout
    # ------------------------------------------------------------------

    def _resolve_history_quantities(self):
        """Aggregate the partner's purchased quantity by product.

        Walks ``sale.order.line`` for the condition's partner in the
        window ``today - history_months_back``, where the window is the
        wizard field (defaulted from Settings, but overridable per print).
        Filters to confirmed or done orders, and to products that are
        still ``active=True`` and ``sale_ok=True`` (so the report is a
        recompra tool, not a full audit log).

        Crucially, this path **does not** apply the
        ``tr_exclude_from_general_pricelist`` filter — the customer
        already bought the product, and the price for a reorder must be
        visible even when the category sits under the "production on
        demand" subtree.

        Returns a dict ``{product.product: qty_in_product_default_uom}``.
        Line UoMs that differ from the product's default are converted
        via ``product_uom._compute_quantity`` before aggregation.
        """
        months = self.history_months_back
        if months <= 0:
            return {}
        partner = self.condition_id.partner_id
        threshold = fields.Date.today() - relativedelta(months=months)
        lines = self.env["sale.order.line"].search(
            [
                ("order_id.partner_id", "=", partner.id),
                ("order_id.state", "in", ("sale", "done")),
                ("order_id.date_order", ">=", threshold),
                ("product_id.active", "=", True),
                ("product_id.sale_ok", "=", True),
            ]
        )
        totals = defaultdict(float)
        for line in lines:
            qty = line.product_uom._compute_quantity(
                line.product_uom_qty, line.product_id.uom_id
            )
            totals[line.product_id] += qty
        return dict(totals)

    def _build_sections_from_history(self, rates):
        """Build report sections for the customer-history layout.

        Two grouping modes:

        - ``variante`` — one row per purchased variant.
        - ``template`` — variants of the same template with the same
          price collapse into a single template row; variants with a
          divergent price surface as ``variant_exceptions``. The
          template row's ``qty`` is the sum across **all** valid
          variants of that template (dominant + divergent), so the
          customer sees the total movement of the template family.
        """
        quantities = self._resolve_history_quantities()
        # Pre-compute pricing and drop invalid-priced variants once,
        # before any aggregation. A variant that won't print must not
        # inflate a template-level qty either.
        pricings = {}
        for product, qty in quantities.items():
            pricing = self._compute_pricing(product, rates)
            if not self._is_valid_price(pricing["price_unit"]):
                continue
            pricings[product] = (pricing, qty)
        if self.history_grouping == "variante":
            return self._build_sections_from_history_by_variant(pricings)
        return self._build_sections_from_history_by_template(pricings)

    def _build_sections_from_history_by_variant(self, pricings):
        """Variant-level history: one row per SKU, no consolidation."""
        depth = self._get_category_depth()
        by_category = defaultdict(list)
        for product, (pricing, qty) in pricings.items():
            grouping = self._resolve_grouping_category(product, depth)
            by_category[grouping].append(
                {
                    "template": product.product_tmpl_id,
                    "product": product,
                    "pricing": pricing,
                    "qty": qty,
                    "uom_label": self._format_uom_label(product.uom_id),
                    # Consolidation doesn't apply here — no inline variants.
                    "variants": [],
                }
            )
        sections = []
        for category in sorted(
            by_category, key=lambda cat: cat.complete_name if cat else ""
        ):
            rows = sorted(
                by_category[category],
                key=lambda row: (
                    row["template"].display_name,
                    row["template"].id,
                    row["product"].display_name,
                    row["product"].id,
                ),
            )
            sections.append(
                {
                    "title": category.name if category else "",
                    "rows": rows,
                    "variant_exceptions": [],
                }
            )
        return sections

    def _build_sections_from_history_by_template(self, pricings):
        """Template-level history: collapse same-price variants per template.

        Reuses ``_consolidate_templates`` from the section builder
        (kept pure — no qty awareness there) and injects the
        history-specific ``qty`` / ``uom_label`` afterwards.
        """
        depth = self._get_category_depth()
        by_category = defaultdict(lambda: self.env["product.product"])
        qty_by_template = defaultdict(float)
        qty_by_variant = {}
        for product, (_pricing, qty) in pricings.items():
            grouping = self._resolve_grouping_category(product, depth)
            by_category[grouping] |= product
            qty_by_template[product.product_tmpl_id] += qty
            qty_by_variant[product] = qty

        def resolver(product):
            return pricings[product][0]

        sections = []
        for category in sorted(
            by_category, key=lambda cat: cat.complete_name if cat else ""
        ):
            rows, variant_exceptions = self._consolidate_templates(
                by_category[category], resolver
            )
            # Group exceptions by template so the QWeb can render each
            # divergent variant inline as a sub-row right under its
            # template — easier to read than a separate "specials"
            # block at the end of the PDF, given that a customer's
            # history typically lists only a handful of templates.
            inline_by_template = defaultdict(list)
            for exc in variant_exceptions:
                product = exc["product"]
                exc["qty"] = qty_by_variant[product]
                exc["uom_label"] = self._format_uom_label(product.uom_id)
                inline_by_template[product.product_tmpl_id].append(exc)
            for row in rows:
                tmpl = row["template"]
                row["qty"] = qty_by_template[tmpl]
                row["uom_label"] = self._format_uom_label(tmpl.uom_id)
                row["inline_exceptions"] = inline_by_template.get(tmpl, [])
            sections.append(
                {
                    "title": category.name if category else "",
                    "rows": rows,
                    # Exceptions are emitted inline below each template
                    # row, not aggregated in the bottom "specials" block.
                    "variant_exceptions": [],
                }
            )
        return sections

    # ------------------------------------------------------------------
    # XLSX flat row builder (always per-variant; ignores history_grouping)
    # ------------------------------------------------------------------

    def _build_xlsx_rows_history(self):
        """Flat per-variant rows for the XLSX export (history layout).

        Bypasses ``_build_sections_from_history`` on purpose: the
        spreadsheet must be 1 row per ``product.product``, no template
        consolidation. Pricing reuses the same ``_compute_pricing``
        engine (qty=1, ``include_inline_bands=False`` to skip the
        band resolver). Sort order: category complete_name → name.
        """
        self.ensure_one()
        rates = get_policy_rates(self.env)
        quantities = self._resolve_history_quantities()
        rows = []
        for product in quantities:
            pricing = self._compute_pricing(product, rates, include_inline_bands=False)
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
    # Entry point used by the report engine
    # ------------------------------------------------------------------

    @api.model
    def _get_report_values(self, docids, data=None):
        wizard = self.browse(docids[0])
        rates = get_policy_rates(self.env)
        if wizard.layout == "historico":
            sections = wizard._build_sections_from_history(rates)
            variant_exceptions = [
                exc for section in sections for exc in section["variant_exceptions"]
            ]
            qty_exceptions = []
        else:
            products = wizard._resolve_products()
            resolver = partial(wizard._compute_pricing, rates=rates)
            sections = wizard._build_sections(
                products, resolver, group_axis=wizard.group_axis
            )
            variant_exceptions = [
                exc for section in sections for exc in section["variant_exceptions"]
            ]
            qty_exceptions = wizard._resolve_qty_exceptions(products, rates)
        has_body = any(section["rows"] for section in sections)
        if not has_body and not variant_exceptions and not qty_exceptions:
            raise UserError(
                _(
                    "No products with valid prices to generate the pricelist. "
                    "Check the pricelist configuration or the invalid price "
                    "threshold setting."
                )
            )
        condition = wizard.condition_id
        partner = condition.partner_id
        # Starting point of the historico window. Read from the wizard
        # (defaulted from Settings, overridable per print). Computed once
        # here so the template (which renders a footer note citing this
        # date) can't drift from the resolver above.
        history_months_back = wizard.history_months_back
        date_history_threshold = (
            fields.Date.today() - relativedelta(months=history_months_back)
            if history_months_back > 0
            else fields.Date.today()
        )
        return {
            "doc_ids": docids,
            "doc_model": "tr.pricelist.report.wizard",
            "docs": wizard,
            "wizard": wizard,
            "condition": condition,
            "partner": partner,
            "company": condition.company_id or self.env.company,
            "date_issued": fields.Date.today(),
            "date_history_threshold": date_history_threshold,
            "history_months_back": history_months_back,
            "discount_display": wizard.discount_display,
            "layout": wizard.layout,
            "history_grouping": wizard.history_grouping,
            "sections": sections,
            "variant_exceptions": variant_exceptions,
            "qty_exceptions": qty_exceptions,
        }

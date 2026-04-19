# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from collections import defaultdict

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
        default="geral",
    )
    group_axis = fields.Selection(
        [("marca", "By Brand"), ("categoria", "By Category")],
        required=True,
        default="marca",
        help="Grouping axis for the General Pricelist layout.",
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
            "When checked, the generated PDF is attached to a mail composer "
            "pre-filled for the partner's email; you can review and edit "
            "before sending. When unchecked, the PDF downloads directly."
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

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_generate(self):
        self.ensure_one()
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
        return self.env.ref(
            "tr_pricelist_report.action_report_pricelist"
        ).report_action(self, config=False)

    def _open_mail_composer(self):
        """Render the PDF, open mail composer with a transient attachment.

        The email "belongs" to the ``partner.commercial.condition`` so
        when actually sent it gets logged in the condition's chatter
        (persistent history). The PDF itself is rendered by an
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
        report = self.env.ref("tr_pricelist_report.action_report_pricelist")
        pdf_content, _content_type = report._render_qweb_pdf(
            report.report_name, self.ids
        )
        partner = condition.partner_id
        attachment = self.env["ir.attachment"].create(
            {
                "name": "Price List - %s.pdf" % (partner.name or ""),
                "type": "binary",
                "datas": base64.b64encode(pdf_content),
                "res_model": "mail.compose.message",
                "res_id": 0,
                "mimetype": "application/pdf",
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

    def _expand_categories(self):
        """Return all categories including descendants of the picked ones."""
        if not self.category_ids:
            return self.env["product.category"]
        return self.env["product.category"].search(
            [("id", "child_of", self.category_ids.ids)]
        )

    def _resolve_products(self):
        domain = [("active", "=", True), ("sale_ok", "=", True)]
        if self.category_ids:
            categories = self._expand_categories()
            domain.append(("categ_id", "in", categories.ids))
        excluded_ids = self._resolve_excluded_category_ids()
        if excluded_ids:
            domain.append(("categ_id", "not in", excluded_ids))
        return self.env["product.product"].search(domain)

    def _resolve_excluded_category_ids(self):
        """Expand the ``tr_exclude_from_general_pricelist`` cascade in one shot.

        Rigid cascade rule (§4.5): any ``product.category`` with the flag
        set, PLUS every descendant of those, is out of scope for the
        general-pricelist layouts (``geral`` Modes A and B). The
        customer-history layout bypasses this
        by resolving products through a different code path.

        The batch expansion uses ``child_of``, which relies on
        ``parent_path`` and runs in a single SQL. ``child_of`` with an
        empty list returns an empty recordset, so the call is safe when
        no category is flagged.
        """
        Category = self.env["product.category"]
        flagged = Category.search([("tr_exclude_from_general_pricelist", "=", True)])
        if not flagged:
            return []
        return Category.search([("id", "child_of", flagged.ids)]).ids

    # ------------------------------------------------------------------
    # Grouping resolvers (shared between layouts)
    # ------------------------------------------------------------------

    def _get_category_depth(self):
        value = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("tr_pricelist_report.category_depth", "-2")
        )
        try:
            return int(value)
        except (TypeError, ValueError):
            return -2

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

    def _get_invalid_price_threshold(self):
        value = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("tr_pricelist_report.invalid_price_threshold", "99999.0")
        )
        try:
            return float(value)
        except (TypeError, ValueError):
            return 99999.0

    def _is_valid_price(self, price_unit):
        threshold = self._get_invalid_price_threshold()
        if threshold <= 0:
            return True
        return price_unit < threshold

    def _get_group_attribute(self):
        """Return the ``product.attribute`` used as the MARCA axis, or empty.

        Resolved at install by the ``post_init_hook`` and cached in
        ``tr_pricelist_report.group_attribute_id``. Empty recordset when
        no attribute with the configured name exists.
        """
        param = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("tr_pricelist_report.group_attribute_id", "")
        )
        if not param or not param.isdigit():
            return self.env["product.attribute"]
        return self.env["product.attribute"].browse(int(param)).exists()

    def _resolve_grouping_category(self, product, depth):
        """Return the category used to bucket ``product`` at ``depth``.

        Clamping is silent (plan §7): if ``depth`` falls outside the
        product's own category trail, we return the closest available
        ancestor. ``-N`` above the root becomes the root; positive level
        above the tree depth becomes the leaf.
        """
        trail = []
        category = product.categ_id
        while category:
            trail.append(category)
            category = category.parent_id
        if not trail:
            return self.env["product.category"]
        # ``trail`` is leaf-first: [leaf, parent, ..., root].
        if depth >= 0:
            root_first = list(reversed(trail))
            return root_first[min(depth, len(root_first) - 1)]
        # Negative: -1 leaf, -2 parent of leaf, ...
        return trail[min(abs(depth) - 1, len(trail) - 1)]

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

    def _compute_pricing(self, product, rates, qty=1.0):
        """Return full pricing dict for a product.

        ``qty`` is forwarded to ``_compute_base_price`` so pricing at a
        quantity tier reflects the ``pricelist_item`` matched for that
        qty — not the unit-level price.
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
        else:
            seller, _extra, _level = condition._resolve_discount_for_product(product)
        price_unit = calc_price_unit(reference, seller, 0.0)
        return {
            "product": product,
            "base": base,
            "reference": reference,
            "seller_discount": seller,
            "price_unit": price_unit,
        }

    def _format_variant_label(self, product):
        """Return a printable label for the inline variant list."""
        attrs = product.product_template_attribute_value_ids.mapped("name")
        if attrs:
            label = " / ".join(attrs)
        else:
            label = product.display_name
        code = product.default_code or ""
        return {"code": code, "label": label}

    def _consolidate_templates(self, products, rates):
        """Group products by template and consolidate same-priced variants.

        Returns tuple ``(rows, variant_exceptions)`` where:

        - ``rows`` is a list of dicts with keys ``template``, ``pricing``,
          ``variants`` (inline variants sharing the dominant price),
          ``default_code`` (template default_code if all inline variants
          collapse to a single display).
        - ``variant_exceptions`` is a list of pricing dicts for variants
          whose ``price_unit`` diverges from the template's dominant price.
        """
        precision = self.env["decimal.precision"].precision_get("Product Price")
        by_template = defaultdict(list)
        for product in products:
            by_template[product.product_tmpl_id].append(product)

        rows = []
        variant_exceptions = []
        for template, variants in by_template.items():
            pricings = [
                p
                for p in (self._compute_pricing(v, rates) for v in variants)
                if self._is_valid_price(p["price_unit"])
            ]
            if not pricings:
                continue
            # Bucket by the **three values the template shows in
            # show_discounts mode** — ``price_unit`` alone would collapse
            # variants that hit the same final price via different base /
            # reference / seller_discount, and the consolidated line would
            # lie about those fields. Bucketing by all three makes the
            # inline-variants row truthful in both ``net_price`` and
            # ``show_discounts`` displays.
            price_buckets = defaultdict(list)
            for pricing in pricings:
                key = (
                    float_round(pricing["price_unit"], precision_digits=precision),
                    float_round(pricing["reference"], precision_digits=precision),
                    float_round(pricing["seller_discount"], precision_digits=2),
                )
                price_buckets[key].append(pricing)
            # dominant = bucket with the most variants; tie: highest price
            dominant_key = max(price_buckets, key=lambda k: (len(price_buckets[k]), k))
            dominant_pricings = price_buckets[dominant_key]
            other_pricings = [
                pricing
                for key, bucket in price_buckets.items()
                if key != dominant_key
                for pricing in bucket
            ]
            rows.append(
                {
                    "template": template,
                    "pricing": dominant_pricings[0],
                    "variants": [
                        self._format_variant_label(p["product"])
                        for p in dominant_pricings
                    ],
                }
            )
            for pricing in other_pricings:
                variant_exceptions.append(pricing)
        rows.sort(key=lambda row: row["template"].display_name)
        variant_exceptions.sort(key=lambda p: p["product"].display_name)
        return rows, variant_exceptions

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
            # Same three-field key used by ``_consolidate_templates`` so the
            # template-collapsed row is truthful in both display modes.
            unique_prices = {
                (
                    float_round(p["price_unit"], precision_digits=precision),
                    float_round(p["reference"], precision_digits=precision),
                    float_round(p["seller_discount"], precision_digits=2),
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

    def _build_sections(self, products, rates):
        """Route products into sections per layout / group_axis.

        - ``geral`` + ``marca`` → partition by MARCA attribute, products
          without MARCA fall back to the category partition at the end.
        - ``geral`` + ``categoria`` → partition by the category at
          ``category_depth`` (shared resolver).
        - ``historico`` uses its own resolver and bypasses this router
          (see ``_build_sections_from_history``).
        """
        if self.layout == "geral" and self.group_axis == "marca":
            return self._build_sections_by_marca(products, rates)
        return self._build_sections_by_category(products, rates)

    def _build_sections_by_category(self, products, rates):
        depth = self._get_category_depth()
        by_category = defaultdict(lambda: self.env["product.product"])
        for product in products:
            by_category[self._resolve_grouping_category(product, depth)] |= product
        sections = []
        for category in sorted(
            by_category, key=lambda cat: cat.complete_name if cat else ""
        ):
            rows, variant_exceptions = self._consolidate_templates(
                by_category[category], rates
            )
            # Skip sections that ended up empty after the invalid-price
            # filter. Otherwise the PDF renders a ghost section with only
            # the title + empty table.
            if not rows and not variant_exceptions:
                continue
            sections.append(
                {
                    "title": category.name if category else "",
                    "rows": rows,
                    "variant_exceptions": variant_exceptions,
                }
            )
        return sections

    def _build_sections_by_marca(self, products, rates):
        """Mode A: group by MARCA attribute value, fallback by category.

        Products with a MARCA value form one section per value. Products
        without the attribute (or when the attribute itself is unresolved)
        fall back to the category resolver used by Mode B — we don't
        introduce a second notion of "category" in the module.
        """
        attribute = self._get_group_attribute()
        by_marca = defaultdict(lambda: self.env["product.product"])
        no_marca = self.env["product.product"]
        if attribute:
            for product in products:
                ptav = product.product_template_attribute_value_ids.filtered(
                    lambda v, attr=attribute: v.attribute_id == attr
                )
                if ptav:
                    by_marca[ptav[0].product_attribute_value_id] |= product
                else:
                    no_marca |= product
        else:
            no_marca = products
        sections = []
        for marca in sorted(by_marca, key=lambda value: value.name or ""):
            rows, variant_exceptions = self._consolidate_templates(
                by_marca[marca], rates
            )
            # Same empty-section guard as _build_sections_by_category.
            if not rows and not variant_exceptions:
                continue
            sections.append(
                {
                    "title": marca.name,
                    "rows": rows,
                    "variant_exceptions": variant_exceptions,
                }
            )
        if no_marca:
            sections.extend(self._build_sections_by_category(no_marca, rates))
        return sections

    # ------------------------------------------------------------------
    # Customer-history layout
    # ------------------------------------------------------------------

    def _resolve_history_quantities(self):
        """Aggregate the partner's purchased quantity by product.

        Walks ``sale.order.line`` for the condition's partner in the
        window ``today - history_months_back``. Filters to confirmed or
        done orders, and to products that are still ``active=True`` and
        ``sale_ok=True`` (so the report is a recompra tool, not a full
        audit log).

        Crucially, this path **does not** apply the
        ``tr_exclude_from_general_pricelist`` filter — the customer
        already bought the product, and the price for a reorder must be
        visible even when the category sits under the "production on
        demand" subtree.

        Returns a dict ``{product.product: qty_in_product_default_uom}``.
        Line UoMs that differ from the product's default are converted
        via ``product_uom._compute_quantity`` before aggregation.
        """
        months = self._get_history_months_back()
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

        One row per purchased variant, grouped by the shared category
        resolver so sections align with the other layouts' depth
        setting. No template consolidation — the customer bought each
        variant specifically, and the report must reflect that.
        """
        quantities = self._resolve_history_quantities()
        depth = self._get_category_depth()
        by_category = defaultdict(list)
        for product, qty in quantities.items():
            pricing = self._compute_pricing(product, rates)
            if not self._is_valid_price(pricing["price_unit"]):
                continue
            grouping = self._resolve_grouping_category(product, depth)
            by_category[grouping].append(
                {
                    "template": product.product_tmpl_id,
                    "product": product,
                    "pricing": pricing,
                    "qty": qty,
                    # "CAIXA" is the common pt_BR uom name; Felipe prefers
                    # the shorter "CX" form used on the product labels and
                    # physical stock. One-liner replace covers every
                    # variation ("CAIXA", "CAIXA COM 10 UNIDADES",
                    # "CAIXA/1000UN", ...).
                    "uom_label": (product.uom_id.name or "").replace("CAIXA", "CX"),
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

    # ------------------------------------------------------------------
    # Entry point used by the report engine
    # ------------------------------------------------------------------

    @api.model
    def _get_report_values(self, docids, data=None):
        wizard = self.browse(docids[0])
        rates = get_policy_rates(self.env)
        if wizard.layout == "historico":
            sections = wizard._build_sections_from_history(rates)
            variant_exceptions = []
            qty_exceptions = []
        else:
            products = wizard._resolve_products()
            sections = wizard._build_sections(products, rates)
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
        # Starting point of the historico window. Computed once here so the
        # template (which renders a footer note citing this date) can't
        # drift from the resolver above.
        history_months_back = wizard._get_history_months_back()
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
            "sections": sections,
            "variant_exceptions": variant_exceptions,
            "qty_exceptions": qty_exceptions,
        }

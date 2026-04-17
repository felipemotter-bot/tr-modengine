# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from collections import defaultdict
from datetime import timedelta

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
        [("por_categoria", "By Category")],
        required=True,
        default="por_categoria",
    )
    category_ids = fields.Many2many(
        "product.category",
        string="Categories",
        help="Pick one or more product categories. Sub-categories are included "
        "automatically.",
    )
    date_end = fields.Date(
        string="Valid Until",
        required=True,
        default=lambda self: self._default_date_end(),
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

    def _default_date_end(self):
        days = int(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("tr_pricelist_report.validity_days", "30")
        )
        return fields.Date.today() + timedelta(days=days)

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
        if not self.category_ids:
            raise UserError(_("Pick at least one product category."))
        # ``config=False`` skips the "configure external layout" wizard that
        # Odoo prompts admins with on first use (returns ir.actions.act_window
        # instead of the report). Pricelist printing shouldn't derail on the
        # layout configurator — the user already asked for a report.
        return self.env.ref(
            "tr_pricelist_report.action_report_pricelist"
        ).report_action(self, config=False)

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
        categories = self._expand_categories()
        return self.env["product.product"].search(
            [
                ("active", "=", True),
                ("sale_ok", "=", True),
                ("categ_id", "in", categories.ids),
            ]
        )

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
            pricings = [self._compute_pricing(v, rates) for v in variants]
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
        """Partition products into sections by category."""
        by_category = defaultdict(lambda: self.env["product.product"])
        for product in products:
            by_category[product.categ_id] |= product
        sections = []
        for category in sorted(by_category, key=lambda cat: cat.complete_name):
            rows, variant_exceptions = self._consolidate_templates(
                by_category[category], rates
            )
            sections.append(
                {
                    "title": category.name,
                    "rows": rows,
                    "variant_exceptions": variant_exceptions,
                }
            )
        return sections

    @api.model
    def _get_report_values(self, docids, data=None):
        wizard = self.browse(docids[0])
        products = wizard._resolve_products()
        rates = get_policy_rates(self.env)
        sections = wizard._build_sections(products, rates)
        variant_exceptions = [
            exc for section in sections for exc in section["variant_exceptions"]
        ]
        qty_exceptions = wizard._resolve_qty_exceptions(products, rates)
        condition = wizard.condition_id
        partner = condition.partner_id
        return {
            "doc_ids": docids,
            "doc_model": "tr.pricelist.report.wizard",
            "docs": wizard,
            "wizard": wizard,
            "condition": condition,
            "partner": partner,
            "company": condition.company_id or self.env.company,
            "date_issued": fields.Date.today(),
            "date_end": wizard.date_end,
            "discount_display": wizard.discount_display,
            "layout": wizard.layout,
            "sections": sections,
            "variant_exceptions": variant_exceptions,
            "qty_exceptions": qty_exceptions,
        }

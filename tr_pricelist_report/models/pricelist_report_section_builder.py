# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from collections import defaultdict

from odoo import models
from odoo.tools.float_utils import float_round


class PricelistReportSectionBuilder(models.AbstractModel):
    """Shared scope/grouping/consolidation helpers for pricelist reports.

    Both ``tr.pricelist.report.wizard`` (General) and
    ``tr.pricelist.basic.wizard`` (Basic) inherit this mixin. The mixin
    does not declare any fields — consumers pass ``category_ids``,
    ``company_id`` and the ``pricing_resolver`` callback as arguments
    so each wizard can keep its own UX and still share the section
    pipeline.
    """

    _name = "tr.pricelist.report.section.builder"
    _description = "Pricelist Report Section Builder"

    # ------------------------------------------------------------------
    # Config helpers (read ir.config_parameter with safe fallbacks)
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
        """Return the ``product.attribute`` used as the MARCA axis, or empty."""
        param = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("tr_pricelist_report.group_attribute_id", "")
        )
        if not param or not param.isdigit():
            return self.env["product.attribute"]
        return self.env["product.attribute"].browse(int(param)).exists()

    # ------------------------------------------------------------------
    # Scope resolution
    # ------------------------------------------------------------------

    def _resolve_excluded_category_ids(self):
        """Expand ``tr_exclude_from_general_pricelist`` into a flat id list.

        Rigid cascade: any flagged category plus every descendant is
        out of scope for all "tipo-geral" printouts (General and Basic).
        The customer-history layout bypasses this by resolving products
        through a different code path.
        """
        Category = self.env["product.category"]
        flagged = Category.search([("tr_exclude_from_general_pricelist", "=", True)])
        if not flagged:
            return []
        return Category.search([("id", "child_of", flagged.ids)]).ids

    def _expand_categories(self, category_ids):
        """Return the picked categories plus all their descendants."""
        if not category_ids:
            return self.env["product.category"]
        return self.env["product.category"].search(
            [("id", "child_of", category_ids.ids)]
        )

    def _resolve_products(self, category_ids=None, company_id=False):
        """Resolve products in scope, optionally filtered by categories/company.

        ``category_ids`` is a ``product.category`` recordset (or False).
        ``company_id`` is the integer id of the company to enforce —
        when truthy the search restricts products to ``company_id IN
        (False, company_id)``; when falsy (the General wizard path)
        no company filter is applied and multi-company concerns are
        enforced elsewhere by the condition record rules.
        """
        domain = [("active", "=", True), ("sale_ok", "=", True)]
        if category_ids:
            categories = self._expand_categories(category_ids)
            domain.append(("categ_id", "in", categories.ids))
        excluded_ids = self._resolve_excluded_category_ids()
        if excluded_ids:
            domain.append(("categ_id", "not in", excluded_ids))
        if company_id:
            domain.append(("company_id", "in", [False, company_id]))
        return self.env["product.product"].search(domain)

    # ------------------------------------------------------------------
    # Grouping
    # ------------------------------------------------------------------

    def _resolve_grouping_category(self, product, depth):
        """Return the category used to bucket ``product`` at ``depth``.

        Clamping is silent: if ``depth`` falls outside the product's
        own category trail, the closest available ancestor is returned
        (``-N`` above the root becomes the root; positive level above
        the tree depth becomes the leaf).
        """
        trail = []
        category = product.categ_id
        while category:
            trail.append(category)
            category = category.parent_id
        if not trail:
            return self.env["product.category"]
        if depth >= 0:
            root_first = list(reversed(trail))
            return root_first[min(depth, len(root_first) - 1)]
        return trail[min(abs(depth) - 1, len(trail) - 1)]

    # ------------------------------------------------------------------
    # Consolidation
    # ------------------------------------------------------------------

    def _format_variant_label(self, product):
        """Return a printable label for the inline variant list."""
        attrs = product.product_template_attribute_value_ids.mapped("name")
        if attrs:
            label = " / ".join(attrs)
        else:
            label = product.display_name
        code = product.default_code or ""
        return {"code": code, "label": label}

    def _consolidate_templates(self, products, pricing_resolver):
        """Group products by template and collapse same-priced variants.

        Returns ``(rows, variant_exceptions)``. ``pricing_resolver`` is
        a callable ``product -> pricing_dict`` returning the full
        pricing contract shared across wizards: ``product``, ``base``,
        ``reference``, ``price_unit``, ``seller_discount``,
        ``simulated_contractual_return``. The bucket key includes all
        four numeric fields so variants that hit the same final price
        through different reference/discount/simulated-return paths
        still show up on their own line.
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
                for p in (pricing_resolver(v) for v in variants)
                if self._is_valid_price(p["price_unit"])
            ]
            if not pricings:
                continue
            price_buckets = defaultdict(list)
            for pricing in pricings:
                # Include ``inline_bands`` in the consolidation key
                # so variants with the same base price but different
                # qty bands don't collapse — otherwise the printed
                # sub-rows would represent only the dominant variant's
                # bands, hiding distinct policies on other variants.
                bands_key = tuple(
                    (
                        float_round(b.get("qty_min", 0.0), precision_digits=2),
                        b.get("qty_uom_label", ""),
                        float_round(b.get("seller_discount", 0.0), precision_digits=2),
                        float_round(b.get("extra_discount", 0.0), precision_digits=2),
                        # Include the computed reference and price_unit
                        # so two variants with same band qty/uom/discounts
                        # but divergent pricelist tiers at that qty don't
                        # consolidate (the printed price would otherwise
                        # be wrong for one of them).
                        float_round(
                            b.get("reference", 0.0), precision_digits=precision
                        ),
                        float_round(
                            b.get("price_unit", 0.0), precision_digits=precision
                        ),
                    )
                    for b in (pricing.get("inline_bands") or [])
                )
                key = (
                    float_round(pricing["price_unit"], precision_digits=precision),
                    float_round(pricing["reference"], precision_digits=precision),
                    float_round(pricing["seller_discount"], precision_digits=2),
                    float_round(
                        pricing["simulated_contractual_return"], precision_digits=2
                    ),
                    bands_key,
                )
                price_buckets[key].append(pricing)
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

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    def _build_sections(self, products, pricing_resolver, group_axis="marca"):
        """Route products into sections per ``group_axis``.

        Historico layout bypasses this router — the General wizard
        calls its own ``_build_sections_from_history`` instead.
        """
        if group_axis == "marca":
            return self._build_sections_by_marca(products, pricing_resolver)
        return self._build_sections_by_category(products, pricing_resolver)

    def _build_sections_by_category(self, products, pricing_resolver):
        depth = self._get_category_depth()
        by_category = defaultdict(lambda: self.env["product.product"])
        for product in products:
            by_category[self._resolve_grouping_category(product, depth)] |= product
        sections = []
        for category in sorted(
            by_category, key=lambda cat: cat.complete_name if cat else ""
        ):
            rows, variant_exceptions = self._consolidate_templates(
                by_category[category], pricing_resolver
            )
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

    def _build_sections_by_marca(self, products, pricing_resolver):
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
                by_marca[marca], pricing_resolver
            )
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
            sections.extend(
                self._build_sections_by_category(no_marca, pricing_resolver)
            )
        return sections

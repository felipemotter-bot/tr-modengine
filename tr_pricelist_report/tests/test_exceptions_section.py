# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import PricelistReportTestCommon


class TestExceptionsSection(PricelistReportTestCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Build a template with two variants, one of which has a specific
        # pricelist_item (variant-level price divergence).
        cls.attribute_color = cls.env["product.attribute"].create(
            {"name": "Color", "create_variant": "always"}
        )
        cls.val_white = cls.env["product.attribute.value"].create(
            {"name": "White", "attribute_id": cls.attribute_color.id}
        )
        cls.val_red = cls.env["product.attribute.value"].create(
            {"name": "Red", "attribute_id": cls.attribute_color.id}
        )
        cls.template_votiva = cls.env["product.template"].create(
            {
                "name": "Vela Votiva 50mm",
                "type": "consu",
                "list_price": 50.0,
                "categ_id": cls.categ_chemicals.id,
                "attribute_line_ids": [
                    (
                        0,
                        0,
                        {
                            "attribute_id": cls.attribute_color.id,
                            "value_ids": [(6, 0, [cls.val_white.id, cls.val_red.id])],
                        },
                    ),
                ],
            }
        )
        # Template-level pricelist item: default R$ 50
        cls.env["product.pricelist.item"].create(
            {
                "pricelist_id": cls.pricelist.id,
                "applied_on": "1_product",
                "product_tmpl_id": cls.template_votiva.id,
                "compute_price": "fixed",
                "fixed_price": 50.0,
            }
        )
        # Locate variants
        cls.variants = cls.template_votiva.product_variant_ids
        cls.variant_white = cls.variants.filtered(
            lambda v: cls.val_white
            in v.product_template_attribute_value_ids.mapped(
                "product_attribute_value_id"
            )
        )
        # Variant-level override: White = R$ 45
        cls.env["product.pricelist.item"].create(
            {
                "pricelist_id": cls.pricelist.id,
                "applied_on": "0_product_variant",
                "product_id": cls.variant_white.id,
                "compute_price": "fixed",
                "fixed_price": 45.0,
            }
        )

    def test_variant_exception_moved_out_of_inline(self):
        """Variant with divergent price goes to exceptions, not inline."""
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        values = wizard._get_report_values(wizard.ids)
        exception_products = [exc["product"] for exc in values["variant_exceptions"]]
        self.assertIn(self.variant_white, exception_products)
        # Non-exception row for the template stays
        votiva_rows = [
            row
            for section in values["sections"]
            for row in section["rows"]
            if row["template"] == self.template_votiva
        ]
        self.assertTrue(votiva_rows)
        inline_codes = [v["code"] for v in votiva_rows[0]["variants"]]
        self.assertNotIn(self.variant_white.default_code, inline_codes)

    def test_qty_tier_exception_listed(self):
        """``pricelist_item`` with min_quantity > 0 shows in qty exceptions."""
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "1_product",
                "product_tmpl_id": self.product_template_b.id,
                "compute_price": "fixed",
                "fixed_price": 180.0,
                "min_quantity": 10,
            }
        )
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        values = wizard._get_report_values(wizard.ids)
        targets = [qex["target"].id for qex in values["qty_exceptions"]]
        self.assertIn(self.product_template_b.id, targets)

    def test_qty_tier_variant_level(self):
        """Variant-level pricelist_item with min_quantity > 0 also listed."""
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "0_product_variant",
                "product_id": self.variant_white.id,
                "compute_price": "fixed",
                "fixed_price": 40.0,
                "min_quantity": 50,
            }
        )
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        values = wizard._get_report_values(wizard.ids)
        # variant-level qty exception present
        variant_qty = [
            qex
            for qex in values["qty_exceptions"]
            if qex["target"] == self.variant_white
        ]
        self.assertEqual(len(variant_qty), 1)
        self.assertEqual(variant_qty[0]["min_qty"], 50)

    def test_qty_tier_splits_per_variant_when_discount_diverges(self):
        """Template tier + per-variant discount emits one row per variant.

        Combination confirmed valid by Felipe (2026-04-17): on the same
        template you can have a quantity tier on the pricelist AND a
        per-variant seller discount on the commercial condition. When
        both apply, printing a single line with the template as target
        would show a price that doesn't match what each variant actually
        costs, so we split.
        """
        # Build a fresh template with two variants and NO variant-level
        # pricelist item, so the tier is the only price driver.
        attr = self.env["product.attribute"].create(
            {"name": "TierColor", "create_variant": "always"}
        )
        val_a = self.env["product.attribute.value"].create(
            {"name": "Alpha", "attribute_id": attr.id}
        )
        val_b = self.env["product.attribute.value"].create(
            {"name": "Beta", "attribute_id": attr.id}
        )
        tmpl = self.env["product.template"].create(
            {
                "name": "Tier Template",
                "type": "consu",
                "list_price": 100.0,
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
                "fixed_price": 40.0,
                "min_quantity": 10,
            }
        )
        variant_alpha = tmpl.product_variant_ids.filtered(
            lambda v: val_a
            in v.product_template_attribute_value_ids.mapped(
                "product_attribute_value_id"
            )
        )
        # Per-variant seller discount only on alpha.
        self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product",
                "product_id": variant_alpha.id,
                "seller_discount": 15.0,
            }
        )
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        values = wizard._get_report_values(wizard.ids)
        tier_rows = [
            qex
            for qex in values["qty_exceptions"]
            if qex["min_qty"] == 10 and qex["target"].id in tmpl.product_variant_ids.ids
        ]
        # One row per variant, not one for the template.
        targets = {row["target"] for row in tier_rows}
        self.assertEqual(len(tier_rows), 2)
        self.assertEqual(targets, set(tmpl.product_variant_ids))
        alpha_row = next(r for r in tier_rows if r["target"] == variant_alpha)
        other_row = next(r for r in tier_rows if r["target"] != variant_alpha)
        # Alpha takes the specific 15% discount; the other variant falls
        # back to the condition's general seller_discount (5% in the
        # test fixture). Alpha must be cheaper.
        self.assertLess(
            alpha_row["pricing"]["price_unit"],
            other_row["pricing"]["price_unit"],
        )

    def test_same_price_unit_via_different_reference_splits(self):
        """Two variants with same price_unit via different ref/seller split.

        Bucket key must include ``reference`` and ``seller_discount`` —
        otherwise a consolidated row in ``show_discounts`` mode would
        lie about those fields. Example: base 100 + seller 10% → 90
        equals base 180 + seller 50% → 90, but the two variants reach
        90 by completely different commercial paths.
        """
        attr = self.env["product.attribute"].create(
            {"name": "MixColor", "create_variant": "always"}
        )
        val_a = self.env["product.attribute.value"].create(
            {"name": "Mix A", "attribute_id": attr.id}
        )
        val_b = self.env["product.attribute.value"].create(
            {"name": "Mix B", "attribute_id": attr.id}
        )
        tmpl = self.env["product.template"].create(
            {
                "name": "Mix Template",
                "type": "consu",
                "list_price": 100.0,
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
        variant_a = tmpl.product_variant_ids.filtered(
            lambda v: val_a
            in v.product_template_attribute_value_ids.mapped(
                "product_attribute_value_id"
            )
        )
        variant_b = tmpl.product_variant_ids - variant_a
        # Variant A: base 100, condition's general 5% → price_unit = 95
        # Variant B: base ~105.263, per-variant discount ~9.75% → 95
        # The base difference comes from two variant-specific pricelist items;
        # the discount difference comes from a per-variant condition line on B.
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "0_product_variant",
                "product_id": variant_a.id,
                "compute_price": "fixed",
                "fixed_price": 100.0,
            }
        )
        # base_b chosen so that 105.263158 * (1 - 0.0975) ≈ 95 (same price_unit)
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "0_product_variant",
                "product_id": variant_b.id,
                "compute_price": "fixed",
                "fixed_price": 105.263158,
            }
        )
        self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product",
                "product_id": variant_b.id,
                "seller_discount": 9.75,
            }
        )
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        values = wizard._get_report_values(wizard.ids)
        tmpl_rows = [
            row
            for section in values["sections"]
            for row in section["rows"]
            if row["template"] == tmpl
        ]
        all_exc_products = {exc["product"] for exc in values["variant_exceptions"]}
        # Both variants should be represented — exactly one in the dominant
        # inline row and the other in variant_exceptions — because their
        # reference / seller_discount differ even though price_unit matches.
        self.assertEqual(len(tmpl_rows), 1)
        self.assertEqual(len(tmpl_rows[0]["variants"]), 1)
        self.assertEqual(len(all_exc_products & set(tmpl.product_variant_ids)), 1)
        # Sanity: price_unit of the inline and the exception must be equal
        # (this is precisely the scenario we're guarding against).
        inline_price = tmpl_rows[0]["pricing"]["price_unit"]
        exc_price = next(
            exc["price_unit"]
            for exc in values["variant_exceptions"]
            if exc["product"] in tmpl.product_variant_ids
        )
        self.assertAlmostEqual(inline_price, exc_price, places=2)

    def test_qty_tier_parity_with_sale_order_line(self):
        """Qty-tier row shows the policy price, parity with sale line."""
        from odoo.tests.common import Form

        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "1_product",
                "product_tmpl_id": self.product_template_b.id,
                "compute_price": "fixed",
                "fixed_price": 180.0,
                "min_quantity": 10,
            }
        )
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        values = wizard._get_report_values(wizard.ids)
        tier_row = next(
            qex
            for qex in values["qty_exceptions"]
            if qex["target"] == self.product_template_b
        )
        # The row's printed price must equal what sale.order.line computes
        # at the same quantity — not the raw ``pricelist_item.fixed_price``.
        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "pricelist_id": self.pricelist.id,
            }
        )
        with Form(order) as form:
            with form.order_line.new() as line:
                line.product_id = self.product_b
                line.product_uom_qty = 10.0
        line = order.order_line[0]
        line._compute_base_price()
        line._compute_reference_price()
        line._compute_price_unit()
        self.assertAlmostEqual(
            tier_row["pricing"]["price_unit"], line.price_unit, places=2
        )
        # And must not equal the raw fixed_price — it must be lower
        # because seller_discount is applied.
        self.assertNotAlmostEqual(tier_row["pricing"]["price_unit"], 180.0, places=2)

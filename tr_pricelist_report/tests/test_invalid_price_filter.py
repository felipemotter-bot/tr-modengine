# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError

from .common import PricelistReportTestCommon


class TestInvalidPriceFilter(PricelistReportTestCommon):
    """Cover the ``invalid_price_threshold`` filter across all paths."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Default threshold is 99999.0; we rely on that unless a test
        # overrides it.
        cls.placeholder_template = cls.env["product.template"].create(
            {
                "name": "Placeholder Product",
                "type": "consu",
                "list_price": 999999.0,
                "categ_id": cls.categ_chemicals.id,
            }
        )
        cls.placeholder_product = cls.placeholder_template.product_variant_ids[0]
        cls.env["product.pricelist.item"].create(
            {
                "pricelist_id": cls.pricelist.id,
                "applied_on": "1_product",
                "product_tmpl_id": cls.placeholder_template.id,
                "compute_price": "fixed",
                "fixed_price": 999999.0,
            }
        )

    def _products_in_rows(self, values):
        """Collect active variants of every template visible in the body.

        Consolidated rows on the non-history layouts carry the
        ``template`` key (there's no ``product``/``target`` key there;
        those keys live in the ``qty_exceptions`` list, not here), so we
        walk the template's variants.
        """
        products = self.env["product.product"]
        for section in values["sections"]:
            for row in section["rows"]:
                template = row.get("template")
                if template and template._name == "product.template":
                    products |= template.product_variant_ids.filtered("active")
        return products

    def test_invalid_price_dropped_from_body(self):
        """Placeholder product (999999) does not appear in the body."""
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        values = wizard._get_report_values(wizard.ids)
        self.assertNotIn(self.placeholder_product, self._products_in_rows(values))

    def test_invalid_price_dropped_from_qty_exceptions(self):
        """Qty-tier exception at a placeholder price doesn't render."""
        # Add a quantity tier at the placeholder price (variant-level so the
        # qty_exceptions builder runs the variant branch).
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "0_product_variant",
                "product_id": self.placeholder_product.id,
                "compute_price": "fixed",
                "fixed_price": 999999.0,
                "min_quantity": 100,
            }
        )
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        values = wizard._get_report_values(wizard.ids)
        targets = [exc.get("target") for exc in values["qty_exceptions"]]
        self.assertNotIn(self.placeholder_product, targets)

    def test_invalid_price_dropped_from_history(self):
        """Customer history layout drops placeholder-priced products too."""
        # Simulate that the partner bought the placeholder product once.
        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "pricelist_id": self.pricelist.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.placeholder_product.id,
                            "product_uom_qty": 10.0,
                            "price_unit": 999999.0,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "product_uom_qty": 3.0,
                            "price_unit": 100.0,
                        },
                    ),
                ],
            }
        )
        order.action_confirm()
        wizard = self._open_wizard(
            category_ids=[self.categ_chemicals.id], layout="historico"
        )
        values = wizard._get_report_values(wizard.ids)
        products_in_history = self.env["product.product"]
        for section in values["sections"]:
            for row in section["rows"]:
                products_in_history |= row["product"]
        self.assertIn(self.product_a, products_in_history)
        self.assertNotIn(self.placeholder_product, products_in_history)

    def test_threshold_zero_disables_filter(self):
        """Threshold set to 0 lets all products through."""
        self.env["ir.config_parameter"].sudo().set_param(
            "tr_pricelist_report.invalid_price_threshold", "0"
        )
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        values = wizard._get_report_values(wizard.ids)
        products = self._products_in_rows(values)
        self.assertIn(self.placeholder_product, products)

    def test_empty_scope_after_filter_raises(self):
        """If every product in scope falls under threshold, raise UserError."""
        # Tighten the threshold so even the legit products fall above it.
        self.env["ir.config_parameter"].sudo().set_param(
            "tr_pricelist_report.invalid_price_threshold", "0.01"
        )
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        with self.assertRaises(UserError):
            wizard._get_report_values(wizard.ids)

    def test_threshold_getter_falls_back_on_invalid_param(self):
        """Non-numeric param value falls back to the 99999.0 default.

        Protects the defensive `except (TypeError, ValueError)` path in
        ``_get_invalid_price_threshold`` from regressions.
        """
        self.env["ir.config_parameter"].sudo().set_param(
            "tr_pricelist_report.invalid_price_threshold", "not_a_number"
        )
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        self.assertEqual(wizard._get_invalid_price_threshold(), 99999.0)

    def test_category_depth_getter_falls_back_on_invalid_param(self):
        """Non-integer param value falls back to -2.

        The settings layer writes valid integers, but a direct edit of
        ``ir.config_parameter`` (ex.: scripts, tests) could leave a
        broken value there. The getter must not crash.
        """
        self.env["ir.config_parameter"].sudo().set_param(
            "tr_pricelist_report.category_depth", "not_a_number"
        )
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        self.assertEqual(wizard._get_category_depth(), -2)

    def test_history_months_getter_falls_back_on_invalid_param(self):
        """Empty / non-integer history_months_back param falls back to 6."""
        self.env["ir.config_parameter"].sudo().set_param(
            "tr_pricelist_report.history_months_back", "not_a_number"
        )
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        self.assertEqual(wizard._get_history_months_back(), 6)

    def test_template_tier_all_variants_invalid_is_dropped(self):
        """Qty tier at the template level is dropped when every variant
        prices invalidly at that quantity.

        Covers the ``if not variant_pricings: continue`` branch in
        ``_resolve_qty_exceptions`` for ``applied_on='1_product'``.
        """
        # Placeholder template tier — every variant prices at 999999 for
        # qty >= 100 → must be dropped by the filter.
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "1_product",
                "product_tmpl_id": self.placeholder_template.id,
                "compute_price": "fixed",
                "fixed_price": 999999.0,
                "min_quantity": 100,
            }
        )
        # Legitimate template tier at a valid price — guarantees the
        # qty_exceptions list has at least one entry, so the assertions
        # below actually run and this stays a real coverage test.
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "1_product",
                "product_tmpl_id": self.product_template_a.id,
                "compute_price": "fixed",
                "fixed_price": 95.0,
                "min_quantity": 100,
            }
        )
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        values = wizard._get_report_values(wizard.ids)
        self.assertTrue(values["qty_exceptions"])
        placeholder_variants = set(self.placeholder_template.product_variant_ids.ids)
        for exc in values["qty_exceptions"]:
            target = exc.get("target")
            self.assertNotEqual(target, self.placeholder_template)
            self.assertNotIn(target.id, placeholder_variants)

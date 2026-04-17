# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import PricelistReportTestCommon


class TestCategoryExclusion(PricelistReportTestCommon):
    """Cover the ``tr_exclude_from_general_pricelist`` cascade on
    ``product.category`` (§4.5 of the plan).

    The cascade is rigid: a category with the flag set excludes itself and
    every descendant from the general-pricelist layouts (``por_categoria``
    and ``geralzao`` Modes A / B). A descendant cannot turn the exclusion
    off. The customer-history layout (PR4) ignores this flag; that
    guarantee is covered on the PR4 test suite.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Custom subtree under Chemicals so we can flag it and watch the
        # cascade without disturbing the unrelated fixtures.
        cls.categ_custom = cls.env["product.category"].create(
            {"name": "Custom", "parent_id": cls.categ_chemicals.id}
        )
        cls.categ_custom_child = cls.env["product.category"].create(
            {"name": "Custom Child", "parent_id": cls.categ_custom.id}
        )
        cls.product_template_custom = cls.env["product.template"].create(
            {
                "name": "Custom Product",
                "type": "consu",
                "list_price": 77.0,
                "categ_id": cls.categ_custom.id,
            }
        )
        cls.product_custom = cls.product_template_custom.product_variant_ids[0]
        cls.product_template_custom_child = cls.env["product.template"].create(
            {
                "name": "Custom Child Product",
                "type": "consu",
                "list_price": 55.0,
                "categ_id": cls.categ_custom_child.id,
            }
        )
        cls.product_custom_child = (
            cls.product_template_custom_child.product_variant_ids[0]
        )
        # Pricelist items so the report finds a price.
        cls.env["product.pricelist.item"].create(
            {
                "pricelist_id": cls.pricelist.id,
                "applied_on": "1_product",
                "product_tmpl_id": cls.product_template_custom.id,
                "compute_price": "fixed",
                "fixed_price": 77.0,
            }
        )
        cls.env["product.pricelist.item"].create(
            {
                "pricelist_id": cls.pricelist.id,
                "applied_on": "1_product",
                "product_tmpl_id": cls.product_template_custom_child.id,
                "compute_price": "fixed",
                "fixed_price": 55.0,
            }
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _products_in_report(self, wizard):
        """Collect every ``product.product`` visible in the rendered report.

        ``_consolidate_templates`` returns one row per template with a
        flattened list of inline variants; we collect every variant of
        every template that reached the report. The variant exceptions
        live on the same templates, so walking ``product_variant_ids``
        of the row template covers them too.
        """
        values = wizard._get_report_values(wizard.ids)
        products = self.env["product.product"]
        for section in values["sections"]:
            for row in section["rows"]:
                products |= row["template"].product_variant_ids.filtered("active")
        for exception in values["variant_exceptions"]:
            products |= exception["product"]
        return products

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_flagged_category_excluded_from_por_categoria(self):
        """Layout ``por_categoria`` drops products whose category is flagged."""
        self.categ_custom.tr_exclude_from_general_pricelist = True
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        products = self._products_in_report(wizard)
        self.assertNotIn(self.product_custom, products)
        # Unrelated products in Chemicals still pass through.
        self.assertIn(self.product_a, products)

    def test_flagged_category_excluded_from_geralzao(self):
        """Modes A and B of ``geralzao`` also drop flagged products."""
        self.categ_custom.tr_exclude_from_general_pricelist = True
        wizard_b = self.env["tr.pricelist.report.wizard"].create(
            {
                "condition_id": self.condition.id,
                "layout": "geralzao",
                "group_axis": "categoria",
            }
        )
        self.assertNotIn(self.product_custom, self._products_in_report(wizard_b))
        wizard_a = self.env["tr.pricelist.report.wizard"].create(
            {
                "condition_id": self.condition.id,
                "layout": "geralzao",
                "group_axis": "marca",
            }
        )
        self.assertNotIn(self.product_custom, self._products_in_report(wizard_a))

    def test_cascade_covers_descendants_even_without_own_flag(self):
        """Descendant category inherits the exclusion (rigid cascade)."""
        # Flag only on the PARENT — the child_of search must catch the
        # grandchild product too.
        self.categ_custom.tr_exclude_from_general_pricelist = True
        self.assertFalse(self.categ_custom_child.tr_exclude_from_general_pricelist)
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        products = self._products_in_report(wizard)
        self.assertNotIn(self.product_custom_child, products)

    def test_leaf_flag_only_excludes_the_leaf_subtree(self):
        """Flag on a child leaves unrelated siblings under the same parent free."""
        # Flag ONLY on the grandchild: its own subtree is out, but
        # ``product_custom`` (sibling-level product directly under
        # Custom) must remain visible.
        self.categ_custom_child.tr_exclude_from_general_pricelist = True
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        products = self._products_in_report(wizard)
        self.assertNotIn(self.product_custom_child, products)
        self.assertIn(self.product_custom, products)

    def test_no_flag_anywhere_preserves_scope(self):
        """With no flagged category, the domain stays unchanged.

        Belt-and-suspenders guard requested by Codex (2026-04-17): lock
        the baseline so future refactors of the exclusion domain don't
        silently widen the filter.
        """
        self.assertFalse(
            self.env["product.category"].search_count(
                [("tr_exclude_from_general_pricelist", "=", True)]
            )
        )
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        products = self._products_in_report(wizard)
        self.assertIn(self.product_a, products)
        self.assertIn(self.product_custom, products)
        self.assertIn(self.product_custom_child, products)

    def test_mixed_selection_filters_only_excluded_subtree(self):
        """Codex 2026-04-17: user picks a category that has one excluded
        subtree and one free subtree. The free side stays, the excluded
        side goes — the filter acts per product, not per section.
        """
        # Flag the grandchild subtree only. ``categ_chemicals`` is the
        # common parent selected by the wizard; it contains:
        #   - ``categ_solvents`` (free, product_b)
        #   - ``categ_custom`` (free, product_custom)
        #     - ``categ_custom_child`` (FLAGGED, product_custom_child)
        self.categ_custom_child.tr_exclude_from_general_pricelist = True
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        products = self._products_in_report(wizard)
        self.assertIn(self.product_a, products)
        self.assertIn(self.product_b, products)
        self.assertIn(self.product_custom, products)
        self.assertNotIn(self.product_custom_child, products)

    def test_resolve_excluded_category_ids_batch_expansion(self):
        """Batch resolver returns the flagged cats + every descendant once."""
        self.categ_custom.tr_exclude_from_general_pricelist = True
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        excluded = wizard._resolve_excluded_category_ids()
        self.assertIn(self.categ_custom.id, excluded)
        self.assertIn(self.categ_custom_child.id, excluded)
        self.assertNotIn(self.categ_chemicals.id, excluded)
        self.assertNotIn(self.categ_solvents.id, excluded)

    def test_resolve_excluded_category_ids_empty_when_none_flagged(self):
        """Safe short-circuit when no category carries the flag."""
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        self.assertEqual(wizard._resolve_excluded_category_ids(), [])

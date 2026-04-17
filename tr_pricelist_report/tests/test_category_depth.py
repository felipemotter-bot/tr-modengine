# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import PricelistReportTestCommon


class TestCategoryDepth(PricelistReportTestCommon):
    """Cover ``_resolve_grouping_category`` clamping semantics (§7)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Build a deeper category tree: A / B / C / D (four levels).
        cls.cat_a = cls.env["product.category"].create({"name": "Cat A"})
        cls.cat_b = cls.env["product.category"].create(
            {"name": "Cat B", "parent_id": cls.cat_a.id}
        )
        cls.cat_c = cls.env["product.category"].create(
            {"name": "Cat C", "parent_id": cls.cat_b.id}
        )
        cls.cat_d = cls.env["product.category"].create(
            {"name": "Cat D", "parent_id": cls.cat_c.id}
        )
        cls.deep_template = cls.env["product.template"].create(
            {
                "name": "Deep",
                "type": "consu",
                "list_price": 10.0,
                "categ_id": cls.cat_d.id,
            }
        )
        cls.deep_product = cls.deep_template.product_variant_ids[0]

    def _wizard(self):
        return self.env["tr.pricelist.report.wizard"].create(
            {"condition_id": self.condition.id, "layout": "geralzao"}
        )

    def test_negative_depth_leaf(self):
        wizard = self._wizard()
        self.assertEqual(
            wizard._resolve_grouping_category(self.deep_product, -1), self.cat_d
        )

    def test_negative_depth_parent_of_leaf(self):
        wizard = self._wizard()
        self.assertEqual(
            wizard._resolve_grouping_category(self.deep_product, -2), self.cat_c
        )

    def test_negative_depth_clamps_to_root(self):
        """``-N`` beyond the trail resolves silently to the root."""
        wizard = self._wizard()
        # Trail has 4 levels; -99 should clamp to the root (A).
        self.assertEqual(
            wizard._resolve_grouping_category(self.deep_product, -99), self.cat_a
        )

    def test_positive_depth_absolute_level(self):
        wizard = self._wizard()
        # depth 0 = root, depth 1 = second level, etc.
        self.assertEqual(
            wizard._resolve_grouping_category(self.deep_product, 0), self.cat_a
        )
        self.assertEqual(
            wizard._resolve_grouping_category(self.deep_product, 2), self.cat_c
        )

    def test_positive_depth_clamps_to_leaf(self):
        """Positive level deeper than the tree clamps to the leaf."""
        wizard = self._wizard()
        self.assertEqual(
            wizard._resolve_grouping_category(self.deep_product, 99), self.cat_d
        )

    def test_no_category(self):
        """Product without ``categ_id`` resolves to empty recordset."""
        orphan_template = self.env["product.template"].create(
            {"name": "Orphan", "type": "consu", "list_price": 5.0}
        )
        # Force categ_id to empty (Odoo sets a default category, clear it).
        orphan_template.categ_id = False
        orphan_product = orphan_template.product_variant_ids[0]
        wizard = self._wizard()
        self.assertFalse(wizard._resolve_grouping_category(orphan_product, -2))

    def test_shallow_tree_clamps_by_product(self):
        """Clamping is per-product: a shallow product clamps independently."""
        # Shallow: a single-level category (just root).
        root_only = self.env["product.category"].create({"name": "Solo"})
        shallow_template = self.env["product.template"].create(
            {
                "name": "Shallow",
                "type": "consu",
                "list_price": 5.0,
                "categ_id": root_only.id,
            }
        )
        shallow_product = shallow_template.product_variant_ids[0]
        wizard = self._wizard()
        # Shallow clamps to its own root
        self.assertEqual(
            wizard._resolve_grouping_category(shallow_product, -5), root_only
        )
        # Deep still resolves to the requested level in its own trail
        self.assertEqual(
            wizard._resolve_grouping_category(self.deep_product, -3), self.cat_b
        )

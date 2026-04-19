# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import SalesRepAccessTestCommon


class TestCatalogSearch(SalesRepAccessTestCommon):
    def test_rep_sees_product_in_allowed_category(self):
        """search([]) on product.product for rep returns allowed products."""
        Product = self.env["product.product"].with_user(self.user_u1)
        ids = Product.search([]).ids
        self.assertIn(self.product.id, ids)
        self.assertIn(self.product_allowed_sub.id, ids)

    def test_rep_does_not_see_product_in_excluded_category(self):
        """Products under excluded subtree stay hidden."""
        Product = self.env["product.product"].with_user(self.user_u1)
        ids = Product.search([]).ids
        self.assertNotIn(self.product_excluded.id, ids)

    def test_rep_does_not_see_product_outside_allowed(self):
        """Products in unrelated categories stay hidden."""
        Product = self.env["product.product"].with_user(self.user_u1)
        ids = Product.search([]).ids
        self.assertNotIn(self.product_other.id, ids)

    def test_rep_sees_nothing_when_no_allowed(self):
        """Fail-safe: agent without allowed categories sees no product."""
        Product = self.env["product.product"].with_user(self.user_u2)
        self.assertFalse(Product.search([]).ids)

    def test_non_rep_user_sees_everything(self):
        """Admin (not rep) is not affected by the filter."""
        ids = self.env["product.product"].search([]).ids
        self.assertIn(self.product.id, ids)
        self.assertIn(self.product_excluded.id, ids)
        self.assertIn(self.product_other.id, ids)

    def test_search_respects_additional_domain(self):
        """Domain passed by the caller is ANDed with the rep filter."""
        Product = self.env["product.product"].with_user(self.user_u1)
        ids = Product.search([("name", "=", "Product Allowed Sub")]).ids
        self.assertEqual(ids, [self.product_allowed_sub.id])

    def test_name_search_respects_catalog(self):
        """name_search delegates to _search and stays filtered."""
        Product = self.env["product.product"].with_user(self.user_u1)
        names = dict(Product.name_search(name=""))
        self.assertIn(self.product.id, names)
        self.assertNotIn(self.product_excluded.id, names)
        self.assertNotIn(self.product_other.id, names)

    def test_product_template_search_filtered(self):
        """product.template._search applies the same rep filter."""
        Template = self.env["product.template"].with_user(self.user_u1)
        ids = Template.search([]).ids
        self.assertIn(self.product.product_tmpl_id.id, ids)
        self.assertNotIn(self.product_excluded.product_tmpl_id.id, ids)

    def test_search_with_limit_and_order_is_still_filtered(self):
        """Limit/order must not let excluded products slip through."""
        Product = self.env["product.product"].with_user(self.user_u1)
        result = Product.search([], limit=100, order="id asc").ids
        self.assertNotIn(self.product_excluded.id, result)
        self.assertNotIn(self.product_other.id, result)

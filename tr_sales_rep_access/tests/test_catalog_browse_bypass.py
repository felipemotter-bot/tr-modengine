# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import SalesRepAccessTestCommon


@tagged("post_install", "-at_install")
class TestCatalogBrowseBypass(SalesRepAccessTestCommon):
    """PR 13 — block direct browse(id).read() for products outside catalog.

    _search already filters rep results. check_access_rule closes the
    bypass where a rep with a known product id calls browse(id).read()
    directly, circumventing _search.
    """

    # ------------------------------------------------------------------
    # product.product
    # ------------------------------------------------------------------

    def test_rep_cannot_read_product_by_id_outside_catalog(self):
        with self.assertRaises(AccessError):
            self.product_other.with_user(self.user_u1).read(["name"])

    def test_rep_cannot_read_excluded_product_by_id(self):
        with self.assertRaises(AccessError):
            self.product_excluded.with_user(self.user_u1).read(["name"])

    def test_rep_can_read_product_by_id_inside_catalog(self):
        data = self.product.with_user(self.user_u1).read(["name"])
        self.assertEqual(data[0]["name"], self.product.name)

    def test_admin_can_read_any_product_by_id(self):
        data = self.product_other.sudo().read(["name"])
        self.assertEqual(data[0]["name"], self.product_other.name)

    # ------------------------------------------------------------------
    # product.template
    # ------------------------------------------------------------------

    def test_rep_cannot_read_product_template_by_id_outside_catalog(self):
        with self.assertRaises(AccessError):
            self.product_other.product_tmpl_id.with_user(self.user_u1).read(["name"])

    def test_rep_can_read_product_template_by_id_inside_catalog(self):
        data = self.product.product_tmpl_id.with_user(self.user_u1).read(["name"])
        self.assertEqual(data[0]["name"], self.product.product_tmpl_id.name)

    # ------------------------------------------------------------------
    # product.category — browse bypass
    # ------------------------------------------------------------------

    def test_rep_cannot_read_category_by_id_outside_catalog(self):
        with self.assertRaises(AccessError):
            self.cat_other.with_user(self.user_u1).read(["name"])

    def test_rep_can_read_category_by_id_inside_catalog(self):
        data = self.cat_allowed.with_user(self.user_u1).read(["name"])
        self.assertEqual(data[0]["name"], self.cat_allowed.name)

    def test_rep_category_search_limited_to_visible(self):
        found = self.env["product.category"].with_user(self.user_u1).search([])
        self.assertIn(self.cat_allowed, found)
        self.assertIn(self.cat_allowed_sub, found)
        self.assertNotIn(self.cat_other, found)
        self.assertNotIn(self.cat_root, found)


@tagged("post_install", "-at_install")
class TestCatalogCheckAccessRuleCoverage(SalesRepAccessTestCommon):
    """PR 13 — coverage for _apply_ir_rules and check_access_rule edge paths.

    check_access_rule is only invoked for non-column (computed) field reads
    or via direct call; stored-field reads go through _apply_ir_rules (SQL
    path). These tests exercise the paths the browse-bypass tests cannot
    reach because they use stored fields.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent_empty = cls.env["res.partner"].create(
            {
                "name": "Agent Empty Catalog",
                "agent": True,
                "sales_profile_id": cls.profile_a1.id,
                "commission_id": cls.commission.id,
            }
        )
        cls.user_empty = cls.env["res.users"].create(
            {
                "name": "Rep Empty Catalog",
                "login": "tsra_rep_empty_cat",
                "partner_id": cls.agent_empty.id,
                "groups_id": [(6, 0, [cls.rep_group.id])],
            }
        )

    # ------------------------------------------------------------------
    # _apply_ir_rules — non-rep user bypasses catalog filter (line 28 pp)
    # ------------------------------------------------------------------

    def test_non_rep_can_read_any_product_product(self):
        """Non-rep internal user is not filtered by catalog rules."""
        admin = self.env.ref("base.user_admin")
        data = self.product_other.with_user(admin).read(["name"])
        self.assertEqual(data[0]["name"], self.product_other.name)

    # ------------------------------------------------------------------
    # _apply_rep_catalog_query / product.product _apply_ir_rules —
    # rep with no catalog → WHERE FALSE added
    # ------------------------------------------------------------------

    def test_empty_rep_cannot_read_product_product_stored_field(self):
        """Rep with empty catalog gets AccessError on product.product read."""
        with self.assertRaises(AccessError):
            self.product.with_user(self.user_empty).read(["name"])

    def test_empty_rep_cannot_read_product_template_stored_field(self):
        """Rep with empty catalog gets AccessError on product.template read."""
        with self.assertRaises(AccessError):
            self.product.product_tmpl_id.with_user(self.user_empty).read(["name"])

    # ------------------------------------------------------------------
    # check_access_rule — non-rep user → early return (line 32/49)
    # ------------------------------------------------------------------

    def test_non_rep_check_access_rule_product_product_passes(self):
        """Non-rep user bypasses check_access_rule for product.product."""
        admin = self.env.ref("base.user_admin")
        self.product_other.with_user(admin).check_access_rule("read")

    def test_non_rep_check_access_rule_product_template_passes(self):
        """Non-rep user bypasses check_access_rule for product.template."""
        admin = self.env.ref("base.user_admin")
        self.product_other.product_tmpl_id.with_user(admin).check_access_rule("read")

    # ------------------------------------------------------------------
    # check_access_rule — rep with no catalog → AccessError (lines 50-54)
    # ------------------------------------------------------------------

    def test_empty_rep_check_access_rule_product_product_raises(self):
        """check_access_rule raises for rep with no visible product.product."""
        with self.assertRaises(AccessError):
            self.product.with_user(self.user_empty).check_access_rule("read")

    def test_empty_rep_check_access_rule_product_template_raises(self):
        """check_access_rule raises for rep with no visible product.template."""
        with self.assertRaises(AccessError):
            self.product.product_tmpl_id.with_user(self.user_empty).check_access_rule(
                "read"
            )

    # ------------------------------------------------------------------
    # check_access_rule — rep with catalog, forbidden record → AccessError
    # ------------------------------------------------------------------

    def test_rep_check_access_rule_forbidden_product_product(self):
        """check_access_rule raises for forbidden product.product."""
        with self.assertRaises(AccessError):
            self.product_other.with_user(self.user_u1).check_access_rule("read")

    def test_rep_check_access_rule_forbidden_product_template(self):
        """check_access_rule raises for forbidden product.template."""
        with self.assertRaises(AccessError):
            self.product_other.product_tmpl_id.with_user(
                self.user_u1
            ).check_access_rule("read")

    # ------------------------------------------------------------------
    # check_access_rule — rep with no catalog, empty recordset → return
    # ------------------------------------------------------------------

    def test_empty_rep_check_access_rule_empty_recordset_product_product(self):
        """check_access_rule on empty recordset with no-catalog rep does not raise."""
        self.env["product.product"].with_user(self.user_empty).check_access_rule("read")

    def test_empty_rep_check_access_rule_empty_recordset_product_template(self):
        """check_access_rule on empty recordset with no-catalog rep does not raise."""
        self.env["product.template"].with_user(self.user_empty).check_access_rule(
            "read"
        )


@tagged("post_install", "-at_install")
class TestCatalogCategoryAncestor(SalesRepAccessTestCommon):
    """PR 13 — ancestor categories readable via relation but not via search.

    When a rep's allowed_category_ids contains only a leaf or mid-level
    node, the parent categories are NOT in visible_ids and must NOT
    appear in search results. But reading categ_id.display_name /
    complete_name on an allowed product must not raise AccessError,
    because the ORM traverses parent_id to build the breadcrumb.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Create an agent whose only allowed category is cat_allowed_sub
        # (a leaf), so cat_allowed and cat_root are ancestors not in visible.
        cls.agent_leaf = cls.env["res.partner"].create(
            {
                "name": "Agent Leaf Only",
                "agent": True,
                "sales_profile_id": cls.profile_a1.id,
                "commission_id": cls.commission.id,
                "allowed_category_ids": [(6, 0, [cls.cat_allowed_sub.id])],
            }
        )
        cls.user_leaf = cls.env["res.users"].create(
            {
                "name": "Rep Leaf",
                "login": "tsra_leaf",
                "partner_id": cls.agent_leaf.id,
                "groups_id": [(6, 0, [cls.rep_group.id])],
            }
        )
        # Product in cat_allowed_sub — visible to user_leaf.
        cls.product_leaf = cls.env["product.product"].create(
            {
                "name": "Product Leaf",
                "type": "consu",
                "list_price": 10.0,
                "invoice_policy": "order",
                "categ_id": cls.cat_allowed_sub.id,
            }
        )

    def test_ancestor_not_in_search_results(self):
        """cat_allowed (parent of visible cat_allowed_sub) must not appear in search."""
        found = self.env["product.category"].with_user(self.user_leaf).search([])
        self.assertIn(self.cat_allowed_sub, found)
        self.assertNotIn(self.cat_allowed, found)
        self.assertNotIn(self.cat_root, found)

    def test_read_visible_category_does_not_raise(self):
        """Direct read of cat_allowed_sub as leaf rep must succeed."""
        self.cat_allowed_sub.with_user(self.user_leaf).read(["name", "display_name"])

    def test_product_categ_id_traversal_does_not_raise(self):
        """Reading product.categ_id (a Many2one) resolves name without error."""
        data = self.product_leaf.with_user(self.user_leaf).read(["categ_id"])
        self.assertEqual(data[0]["categ_id"][0], self.cat_allowed_sub.id)
        self.assertTrue(data[0]["categ_id"][1])  # name_get resolved without error

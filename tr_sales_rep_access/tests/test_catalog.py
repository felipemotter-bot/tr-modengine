# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import SalesRepAccessTestCommon

DEFAULT_CATALOG_PARAM = "tr_sales_rep_access.tr_sales_rep_default_category_ids"


class TestCatalogModel(SalesRepAccessTestCommon):
    def test_visible_categories_expand_allowed(self):
        """allowed includes its descendants; the parent itself is visible."""
        visible = self.agent_a1._get_visible_category_ids()
        self.assertIn(self.cat_allowed, visible)
        self.assertIn(self.cat_allowed_sub, visible)

    def test_visible_categories_remove_excluded_subtree(self):
        """excluded subtree is removed even though its parent is allowed."""
        visible = self.agent_a1._get_visible_category_ids()
        self.assertNotIn(self.cat_excluded_sub, visible)

    def test_visible_categories_do_not_include_unrelated(self):
        """Categories outside the allowed subtree stay invisible."""
        visible = self.agent_a1._get_visible_category_ids()
        self.assertNotIn(self.cat_other, visible)
        self.assertNotIn(self.cat_root, visible)

    def test_visible_categories_empty_when_allowed_empty(self):
        """Fail-safe: agent without allowed categories sees nothing."""
        self.assertFalse(self.agent_a2.allowed_category_ids)
        self.assertFalse(self.agent_a2._get_visible_category_ids())

    def test_visible_categories_ignores_excluded_when_allowed_empty(self):
        """Short-circuit: no allowed → no visible, even with excluded set."""
        self.agent_a2.write({"excluded_category_ids": [(6, 0, [self.cat_other.id])]})
        self.assertFalse(self.agent_a2._get_visible_category_ids())

    def test_create_agent_applies_default_catalog(self):
        """New agent created without allowed_category_ids inherits settings default."""
        value = str(self.cat_allowed.id)
        self.env["ir.config_parameter"].sudo().set_param(DEFAULT_CATALOG_PARAM, value)
        profile = self._make_sales_profile("A3 Profile")
        agent = self.env["res.partner"].create(
            {
                "name": "Agent A3",
                "agent": True,
                "sales_profile_id": profile.id,
                "commission_id": self.commission.id,
            }
        )
        self.assertIn(self.cat_allowed, agent.allowed_category_ids)

    def test_create_agent_explicit_catalog_is_not_overwritten(self):
        """Caller-provided allowed_category_ids wins over the default."""
        self.env["ir.config_parameter"].sudo().set_param(
            DEFAULT_CATALOG_PARAM, str(self.cat_allowed.id)
        )
        profile = self._make_sales_profile("A4 Profile")
        agent = self.env["res.partner"].create(
            {
                "name": "Agent A4",
                "agent": True,
                "sales_profile_id": profile.id,
                "commission_id": self.commission.id,
                "allowed_category_ids": [(6, 0, [self.cat_other.id])],
            }
        )
        self.assertEqual(agent.allowed_category_ids, self.cat_other)

    def test_create_non_agent_ignores_default_catalog(self):
        """agent=False does not trigger the default catalog."""
        self.env["ir.config_parameter"].sudo().set_param(
            DEFAULT_CATALOG_PARAM, str(self.cat_allowed.id)
        )
        partner = self.env["res.partner"].create({"name": "Just a Customer"})
        self.assertFalse(partner.allowed_category_ids)

    def test_default_catalog_ignores_stale_ids(self):
        """Param referencing a deleted category is silently dropped."""
        stale_cat = self.env["product.category"].create(
            {"name": "Stale", "parent_id": self.cat_root.id}
        )
        self.env["ir.config_parameter"].sudo().set_param(
            DEFAULT_CATALOG_PARAM,
            f"{self.cat_allowed.id},{stale_cat.id}",
        )
        stale_cat.unlink()
        profile = self._make_sales_profile("A5 Profile")
        agent = self.env["res.partner"].create(
            {
                "name": "Agent A5",
                "agent": True,
                "sales_profile_id": profile.id,
                "commission_id": self.commission.id,
            }
        )
        self.assertEqual(agent.allowed_category_ids, self.cat_allowed)

    def test_default_catalog_ignores_non_integer_param(self):
        """Garbled param value is treated as empty (no default applied)."""
        self.env["ir.config_parameter"].sudo().set_param(
            DEFAULT_CATALOG_PARAM, "not-a-csv-of-ints"
        )
        profile = self._make_sales_profile("A6 Profile")
        agent = self.env["res.partner"].create(
            {
                "name": "Agent A6",
                "agent": True,
                "sales_profile_id": profile.id,
                "commission_id": self.commission.id,
            }
        )
        self.assertFalse(agent.allowed_category_ids)

    def test_config_settings_roundtrip(self):
        """get_values/set_values preserve the configured categories."""
        Settings = self.env["res.config.settings"]
        settings = Settings.create(
            {
                "tr_sales_rep_default_category_ids": [
                    (
                        6,
                        0,
                        [self.cat_allowed.id, self.cat_other.id],
                    )
                ],
            }
        )
        settings.execute()
        # Read back via a fresh settings record.
        read_settings = Settings.create({})
        self.assertIn(
            self.cat_allowed,
            read_settings.tr_sales_rep_default_category_ids,
        )
        self.assertIn(
            self.cat_other,
            read_settings.tr_sales_rep_default_category_ids,
        )

    def test_visible_categories_without_excluded_returns_descendants(self):
        """Agent with allowed and no excluded sees the full subtree."""
        self.agent_a1.write({"excluded_category_ids": [(6, 0, [])]})
        visible = self.agent_a1._get_visible_category_ids()
        self.assertIn(self.cat_allowed, visible)
        self.assertIn(self.cat_allowed_sub, visible)
        # Without exclusions, the previously-excluded subtree comes back.
        self.assertIn(self.cat_excluded_sub, visible)

    def test_config_settings_get_values_with_garbled_param(self):
        """get_values() drops a non-integer param without raising."""
        self.env["ir.config_parameter"].sudo().set_param(
            DEFAULT_CATALOG_PARAM, "foo,bar,baz"
        )
        settings = self.env["res.config.settings"].create({})
        self.assertFalse(settings.tr_sales_rep_default_category_ids)

    def test_config_settings_empty_roundtrip(self):
        """Setting empty value clears the stored param."""
        self.env["ir.config_parameter"].sudo().set_param(
            DEFAULT_CATALOG_PARAM, str(self.cat_allowed.id)
        )
        settings = self.env["res.config.settings"].create(
            {"tr_sales_rep_default_category_ids": [(6, 0, [])]}
        )
        settings.execute()
        stored = self.env["ir.config_parameter"].sudo().get_param(DEFAULT_CATALOG_PARAM)
        # Odoo deletes the param row when ``set_param`` is given an empty
        # string, so ``get_param`` then returns the default ``False``.
        # Either value is acceptable — both mean "no default catalog".
        self.assertFalse(stored)

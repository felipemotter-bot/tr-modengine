# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError

from .common import SalesRepAccessTestCommon


class TestSecurity(SalesRepAccessTestCommon):
    def test_group_implies_only_base_user(self):
        """Rep group implies base.group_user and no Sales/Account/export."""
        implied = self.rep_group.implied_ids
        self.assertIn(self.env.ref("base.group_user"), implied)

        forbidden_xmlids = [
            "sales_team.group_sale_salesman",
            "account.group_account_invoice",
            "account.group_account_readonly",
            "base.group_allow_export",
            "base.group_system",
        ]
        for xmlid in forbidden_xmlids:
            group = self.env.ref(xmlid)
            all_implied = self.rep_group.implied_ids
            self.assertNotIn(
                group,
                all_implied,
                f"Rep group must NOT imply {xmlid} " f"(found it in implied_ids).",
            )
            self.assertNotIn(
                group,
                self.rep_group.trans_implied_ids,
                f"Rep group must NOT transitively imply {xmlid}.",
            )

    def test_rep_user_does_not_have_export_group(self):
        """Rep user is NOT member of base.group_allow_export."""
        export_group = self.env.ref("base.group_allow_export")
        self.assertNotIn(
            export_group,
            self.user_u1.groups_id,
            "Rep user must NOT be in base.group_allow_export.",
        )

    def test_rep_user_not_in_forbidden_groups(self):
        """Rep users must not belong to any sensitive group via direct membership.

        Complements test_group_implies_only_base_user (which checks static
        implied_ids). A future module could add the rep user to a group via
        res.users.groups_id without touching implied_ids — this test catches
        that regression at the user level.

        stock.group_stock_user is included because PR 9 relies on the rep
        not being in that group to keep the Inventory tab, smart buttons
        and stock menus hidden.
        """
        forbidden_xmlids = [
            "sales_team.group_sale_salesman",
            "account.group_account_invoice",
            "account.group_account_readonly",
            "base.group_allow_export",
            "base.group_system",
            "stock.group_stock_user",
            "stock.group_stock_manager",
            "eng_partner_sales_info.group_partner_sales_analysis",
        ]
        for xmlid in forbidden_xmlids:
            group = self.env.ref(xmlid)
            self.assertNotIn(
                group,
                self.user_u1.groups_id,
                f"Rep user_u1 must NOT be a member of {xmlid}.",
            )
            self.assertNotIn(
                group,
                self.user_u2.groups_id,
                f"Rep user_u2 must NOT be a member of {xmlid}.",
            )

    def test_rep_cannot_unlink_sale_order(self):
        """ACL blocks rep from unlink of sale.order."""
        order = self._make_order(self.customer_c1)
        with self.assertRaises(AccessError):
            order.with_user(self.user_u1).unlink()

    def test_rep_does_not_see_admin_menus(self):
        """Menus of modules the rep group does NOT imply stay invisible.

        Validates the DA-4 decision to drop the ``groups="!..."`` menu
        override: because the rep group only implies ``base.group_user``,
        menus that require Sales/Account/Stock groups are filtered out
        by Odoo's additive ACL on ``ir.ui.menu`` naturally.

        If any of the listed xmlids no longer exists in the project,
        ``env.ref`` raises — a clear signal to update this list.
        """
        Menu = self.env["ir.ui.menu"].with_user(self.user_u1)
        hidden_xmlids = [
            "stock.menu_stock_root",
            "account.menu_finance",
            "sale.menu_sale_config",
        ]
        for xmlid in hidden_xmlids:
            menu = self.env.ref(xmlid)
            self.assertFalse(
                Menu.search([("id", "=", menu.id)]),
                f"Menu {xmlid} must not be visible to the rep user.",
            )

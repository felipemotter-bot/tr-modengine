# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import SalesRepAccessTestCommon


@tagged("post_install", "-at_install")
class TestRepFiscalAndMenus(SalesRepAccessTestCommon):
    """Cover the changes in this PR:

    - fiscal lookup ACLs (cfop, operation.line, tax)
    - hidden child-model One2many fields (analytic_line_ids,
      purchase_line_ids)
    - product_updatable sudo compute
    - agent_ids readonly for reps on partner form
    - partner visibility rule excludes other agent partners
    - top-level menus hidden (HR / Calendar / Dashboards)
    - Contacts menu accessible to reps
    """

    # ------------------------------------------------------------------
    # Fiscal lookup ACLs
    # ------------------------------------------------------------------

    def test_rep_can_read_fiscal_tax(self):
        self.env["l10n_br_fiscal.tax"].with_user(self.user_u1).search([], limit=1)

    def test_rep_can_read_fiscal_cfop(self):
        self.env["l10n_br_fiscal.cfop"].with_user(self.user_u1).search([], limit=1)

    def test_rep_can_read_fiscal_operation_line(self):
        self.env["l10n_br_fiscal.operation.line"].with_user(self.user_u1).search(
            [], limit=1
        )

    def test_rep_cannot_write_fiscal_tax(self):
        tax = self.env["l10n_br_fiscal.tax"].sudo().search([], limit=1)
        if tax:
            with self.assertRaises(AccessError):
                tax.with_user(self.user_u1).write({"name": "x"})

    # ------------------------------------------------------------------
    # Hidden One2many fields (reps can't read them at all)
    # ------------------------------------------------------------------

    def test_rep_cannot_read_analytic_line_ids_on_sale_order_line(self):
        fields_info = self.env["sale.order.line"].with_user(self.user_u1).fields_get()
        self.assertNotIn(
            "analytic_line_ids",
            fields_info,
            "analytic_line_ids must be hidden from reps via groups=!rep",
        )

    def test_rep_cannot_read_purchase_line_ids_on_sale_order_line(self):
        fields_info = self.env["sale.order.line"].with_user(self.user_u1).fields_get()
        self.assertNotIn(
            "purchase_line_ids",
            fields_info,
            "purchase_line_ids must be hidden from reps via groups=!rep",
        )

    def test_rep_cannot_read_unmanaged_commission_warning_on_order(self):
        fields_info = self.env["sale.order"].with_user(self.user_u1).fields_get()
        self.assertNotIn(
            "unmanaged_commission_warning",
            fields_info,
            "unmanaged_commission_warning must be hidden for reps",
        )

    # ------------------------------------------------------------------
    # product_updatable stays readable (sudo compute)
    # ------------------------------------------------------------------

    def test_rep_can_read_product_updatable(self):
        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer_c1.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product.id,
                            "product_uom_qty": 1.0,
                        },
                    ),
                ],
            }
        )
        line = order.order_line
        # Must not raise — sudo override runs on rep's behalf.
        self.assertIn(
            line.with_user(self.user_u1).product_updatable,
            (True, False),
        )

    # ------------------------------------------------------------------
    # agent_ids readonly on partner form for reps
    # ------------------------------------------------------------------

    def test_rep_partner_form_marks_agent_ids_readonly(self):
        arch, _ = (
            self.env["res.partner"].with_user(self.user_u1)._get_view(view_type="form")
        )
        nodes = arch.xpath("//field[@name='agent_ids']")
        self.assertTrue(nodes, "agent_ids must be in the rep's partner form arch")
        for node in nodes:
            modifiers = json.loads(node.get("modifiers") or "{}")
            self.assertTrue(
                modifiers.get("readonly"),
                "agent_ids should be readonly for reps to prevent customer transfer",
            )

    # ------------------------------------------------------------------
    # Partner record rule: another rep's agent partner is not visible
    # ------------------------------------------------------------------

    def test_rep_cannot_see_another_rep_agent_partner(self):
        # agent_a2 is a different agent partner, assigned to user_u2
        found = (
            self.env["res.partner"]
            .with_user(self.user_u1)
            .search([("id", "=", self.agent_a2.id)])
        )
        self.assertFalse(
            found,
            "A rep should not be able to see another rep's agent partner — "
            "the record rule excludes agent partners from the user-partner "
            "visibility broadener.",
        )

    # ------------------------------------------------------------------
    # Menu visibility: HR / Calendar / Dashboards hidden, Contacts visible
    # ------------------------------------------------------------------

    def _menu_visible_to_rep(self, xmlid):
        menu = self.env.ref(xmlid)
        found = (
            self.env["ir.ui.menu"]
            .with_user(self.user_u1)
            .search([("id", "=", menu.id)])
        )
        return bool(found)

    def test_hr_root_menu_hidden_for_rep(self):
        self.assertFalse(self._menu_visible_to_rep("hr.menu_hr_root"))

    def test_calendar_menu_hidden_for_rep(self):
        self.assertFalse(self._menu_visible_to_rep("calendar.mail_menu_calendar"))

    def test_spreadsheet_dashboard_menu_hidden_for_rep(self):
        self.assertFalse(
            self._menu_visible_to_rep(
                "spreadsheet_dashboard.spreadsheet_dashboard_menu_root"
            )
        )

    def test_contacts_menu_visible_for_rep(self):
        self.assertTrue(self._menu_visible_to_rep("contacts.res_partner_menu_contacts"))

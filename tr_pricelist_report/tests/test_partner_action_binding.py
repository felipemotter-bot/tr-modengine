# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError

from .common import PricelistReportTestCommon


class TestPartnerActionBinding(PricelistReportTestCommon):
    """Server action binding that exposes ``Print Price List`` in the
    partner's Action menu."""

    def test_server_action_returns_wizard(self):
        """Action binding calls res.partner.action_print_pricelist()."""
        action = self.env.ref("tr_pricelist_report.action_partner_print_pricelist")
        # Simulate what Odoo does when the user clicks the action menu entry:
        # ``records`` bound in the action's code.
        run_ctx = dict(
            active_model="res.partner",
            active_ids=[self.customer.id],
            active_id=self.customer.id,
        )
        result = action.with_context(**run_ctx).run()
        self.assertEqual(result["type"], "ir.actions.act_window")
        self.assertEqual(result["res_model"], "tr.pricelist.report.wizard")
        ctx = result["context"]
        self.assertEqual(ctx["default_condition_id"], self.condition.id)

    def test_action_binding_is_on_res_partner(self):
        """Binding model and view types are set so it shows up in Action."""
        action = self.env.ref("tr_pricelist_report.action_partner_print_pricelist")
        self.assertEqual(action.binding_model_id.model, "res.partner")
        self.assertEqual(action.binding_type, "action")
        self.assertIn("form", action.binding_view_types)
        self.assertIn("list", action.binding_view_types)

    def test_multi_select_raises_user_error(self):
        """Selecting multiple partners in the list raises a friendly error.

        Covers the guard Codex flagged: ``action_print_pricelist`` uses
        ``ensure_one()``, so without an upstream check a multi-select would
        crash with a generic singleton traceback. The server action's code
        normalizes that to a clear ``UserError``.
        """
        extra_partner = self.env["res.partner"].create({"name": "Another Partner"})
        action = self.env.ref("tr_pricelist_report.action_partner_print_pricelist")
        run_ctx = dict(
            active_model="res.partner",
            active_ids=[self.customer.id, extra_partner.id],
            active_id=self.customer.id,
        )
        with self.assertRaises(UserError):
            action.with_context(**run_ctx).run()

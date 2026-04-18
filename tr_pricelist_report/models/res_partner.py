# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, models
from odoo.exceptions import UserError


class ResPartner(models.Model):
    _inherit = "res.partner"

    def action_print_pricelist(self):
        self.ensure_one()
        condition = self.effective_condition_id
        if not condition:
            raise UserError(
                _("Partner %s has no commercial condition.") % self.display_name
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Print Price List"),
            "res_model": "tr.pricelist.report.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "active_model": "res.partner",
                "active_id": self.id,
                "default_condition_id": condition.id,
            },
        }

    def action_print_pricelist_from_menu(self):
        """Entry point used by the Action menu server action.

        The menu is exposed on ``form`` and ``list`` view types — when the
        user selects multiple partners on the list, we surface a friendly
        UserError instead of the generic ``ensure_one()`` singleton
        traceback raised by :meth:`action_print_pricelist`.
        """
        if len(self) != 1:
            raise UserError(_("Select a single partner to print the price list."))
        return self.action_print_pricelist()

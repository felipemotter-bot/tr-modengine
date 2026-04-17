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

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, models


class PartnerCommercialCondition(models.Model):
    _inherit = "partner.commercial.condition"

    def action_print_pricelist(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Print Price List"),
            "res_model": "tr.pricelist.report.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "active_model": "partner.commercial.condition",
                "active_id": self.id,
                "default_condition_id": self.id,
            },
        }

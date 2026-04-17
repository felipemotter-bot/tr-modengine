# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class GroupConditionWizard(models.TransientModel):
    _name = "tr.group.condition.wizard"
    _description = "Group Condition Conflict Wizard"

    partner_id = fields.Many2one("res.partner", required=True, readonly=True)
    group_id = fields.Many2one("res.partner", readonly=True)
    own_condition_id = fields.Many2one(
        "partner.commercial.condition",
        string="Current Own Condition",
        readonly=True,
    )
    group_condition_id = fields.Many2one(
        "partner.commercial.condition",
        string="Group Condition",
        readonly=True,
    )
    action = fields.Selection(
        [
            ("keep", "Keep own condition (override group)"),
            ("inherit", "Discard own condition (inherit from group)"),
        ],
        default="keep",
        required=True,
    )

    def action_confirm(self):
        self.ensure_one()
        if self.action == "inherit":
            self.partner_id.commercial_condition_id = False
        # If "keep", the partner already has its own condition — nothing to do.
        return {"type": "ir.actions.act_window_close"}

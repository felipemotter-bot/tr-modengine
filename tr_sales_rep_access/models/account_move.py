# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models

_GROUPS_NO_REP = "!tr_sales_rep_access.group_sales_rep_external"


class AccountMove(models.Model):
    _inherit = "account.move"

    sales_rep_partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Sales Rep",
        domain=[("agent", "=", True)],
        copy=False,
        index=True,
        readonly=True,
        help=(
            "Commercial agent responsible for this invoice, propagated "
            "from the originating sale order snapshot."
        ),
    )

    # PR 8 / PR 12 — chatter hidden for reps. PR 12 extends to auxiliary
    # mail.thread fields so that metadata is also blocked via RPC.
    message_ids = fields.One2many(groups=_GROUPS_NO_REP)
    message_follower_ids = fields.One2many(groups=_GROUPS_NO_REP)
    message_is_follower = fields.Boolean(groups=_GROUPS_NO_REP)
    message_partner_ids = fields.Many2many(groups=_GROUPS_NO_REP)
    has_message = fields.Boolean(groups=_GROUPS_NO_REP)
    message_needaction = fields.Boolean(groups=_GROUPS_NO_REP)
    message_needaction_counter = fields.Integer(groups=_GROUPS_NO_REP)
    message_has_error = fields.Boolean(groups=_GROUPS_NO_REP)
    message_has_error_counter = fields.Integer(groups=_GROUPS_NO_REP)
    message_attachment_count = fields.Integer(groups=_GROUPS_NO_REP)
    website_message_ids = fields.One2many(groups=_GROUPS_NO_REP)

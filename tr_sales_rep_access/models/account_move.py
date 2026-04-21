# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


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

    # PR 8 — chatter hidden for reps (same pattern as sale.order).
    message_ids = fields.One2many(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    message_follower_ids = fields.One2many(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )

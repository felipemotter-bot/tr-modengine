# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import pytz

from odoo import api, fields, models

from .stock_picking import BYPASS_CONTEXT_KEY


class SaleOrder(models.Model):
    _inherit = "sale.order"

    next_planned_delivery_date = fields.Date(
        compute="_compute_next_planned_delivery_date",
        string="Next Planned Delivery",
        help="Planned delivery date of the next pending outgoing picking "
        "linked to this sale order.",
    )

    @api.depends(
        "picking_ids.planned_delivery_date",
        "picking_ids.state",
        "picking_ids.is_sale_delivery_planning_applicable",
    )
    def _compute_next_planned_delivery_date(self):
        for order in self:
            pending = order.picking_ids.filtered(
                lambda p: p.state not in ("done", "cancel")
                and p.is_sale_delivery_planning_applicable
                and p.planned_delivery_date
            ).sorted("planned_delivery_date")
            order.next_planned_delivery_date = (
                pending[0].planned_delivery_date if pending else False
            )

    def _action_confirm(self):
        res = super()._action_confirm()
        for order in self.filtered("commitment_date"):
            applicable_pickings = order.picking_ids.filtered(
                lambda p: p.is_sale_delivery_planning_applicable
                and not p.planned_delivery_date
            )
            if not applicable_pickings:
                continue
            tz = pytz.timezone(self.env.user.tz or "UTC")
            commitment_utc = order.commitment_date.replace(tzinfo=pytz.utc)
            planned_date = commitment_utc.astimezone(tz).date()
            applicable_pickings.with_context(**{BYPASS_CONTEXT_KEY: True}).write(
                {"planned_delivery_date": planned_date}
            )
        return res

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import datetime, time

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

PLANNER_GROUP_XMLID = "tr_sale_delivery_planning.group_delivery_planner"
BYPASS_CONTEXT_KEY = "bypass_delivery_planning_acl"


class StockPicking(models.Model):
    _inherit = "stock.picking"

    planned_delivery_date = fields.Date(
        index=True,
        tracking=True,
        copy=False,
        help="Date promised by the planner for this delivery. When set, "
        "synchronizes scheduled_date to end-of-day in the user's timezone.",
    )
    is_delivery_planned = fields.Boolean(
        compute="_compute_is_delivery_planned",
        store=True,
        index=True,
    )
    is_sale_delivery_planning_applicable = fields.Boolean(
        compute="_compute_is_sale_delivery_planning_applicable",
        store=True,
        index=True,
    )

    @api.depends("planned_delivery_date")
    def _compute_is_delivery_planned(self):
        for picking in self:
            picking.is_delivery_planned = bool(picking.planned_delivery_date)

    @api.depends(
        "sale_id",
        "picking_type_id.code",
        "location_id.usage",
        "location_dest_id.usage",
    )
    def _compute_is_sale_delivery_planning_applicable(self):
        for picking in self:
            picking.is_sale_delivery_planning_applicable = bool(
                picking.sale_id
                and picking.picking_type_id.code == "outgoing"
                and picking.location_id.usage == "internal"
                and picking.location_dest_id.usage == "customer"
            )

    def _check_delivery_planner_acl(self):
        if self.env.context.get(BYPASS_CONTEXT_KEY):
            return
        if self.env.user.has_group(PLANNER_GROUP_XMLID):
            return
        raise AccessError(
            _(
                "Only members of the 'Delivery Planner' group can set the "
                "planned delivery date."
            )
        )

    @api.model_create_multi
    def create(self, vals_list):
        if any("planned_delivery_date" in vals for vals in vals_list):
            self._check_delivery_planner_acl()
        pickings = super().create(vals_list)
        pickings_with_planned = pickings.filtered("planned_delivery_date")
        if pickings_with_planned:
            pickings_with_planned._sync_scheduled_date_from_planned()
        return pickings

    def write(self, vals):
        if "planned_delivery_date" in vals:
            self._check_delivery_planner_acl()
        res = super().write(vals)
        if "planned_delivery_date" in vals:
            self._sync_scheduled_date_from_planned()
        return res

    def _sync_scheduled_date_from_planned(self):
        tz = pytz.timezone(self.env.user.tz or "UTC")
        for picking in self:
            if not picking.planned_delivery_date:
                continue
            local_dt = tz.localize(
                datetime.combine(picking.planned_delivery_date, time(23, 59, 59))
            )
            picking.scheduled_date = local_dt.astimezone(pytz.utc).replace(tzinfo=None)

    @api.depends(
        "date_deadline",
        "scheduled_date",
        "is_sale_delivery_planning_applicable",
        "is_delivery_planned",
    )
    def _compute_has_deadline_issue(self):
        res = super()._compute_has_deadline_issue()
        self.filtered(
            lambda p: p.is_sale_delivery_planning_applicable
            and not p.is_delivery_planned
        ).has_deadline_issue = False
        return res

    @api.depends(
        "state",
        "picking_type_code",
        "scheduled_date",
        "move_ids",
        "move_ids.forecast_availability",
        "move_ids.forecast_expected_date",
        "is_sale_delivery_planning_applicable",
        "is_delivery_planned",
    )
    def _compute_products_availability(self):
        res = super()._compute_products_availability()
        to_clear = self.filtered(
            lambda p: p.is_sale_delivery_planning_applicable
            and not p.is_delivery_planned
        )
        to_clear.products_availability = False
        to_clear.products_availability_state = False
        return res

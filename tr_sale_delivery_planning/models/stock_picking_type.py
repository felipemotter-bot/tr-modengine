# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import time

from odoo import models
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT


class StockPickingType(models.Model):
    _inherit = "stock.picking.type"

    def _compute_picking_count(self):
        res = super()._compute_picking_count()
        # Recompute count_picking_late excluding sale-applicable pickings
        # without a planned delivery date — those are "not yet operational"
        # rather than "late", per tr_sale_delivery_planning architecture.
        late_domain = [
            ("scheduled_date", "<", time.strftime(DEFAULT_SERVER_DATETIME_FORMAT)),
            ("state", "in", ("assigned", "waiting", "confirmed")),
            ("picking_type_id", "in", self.ids),
            "|",
            ("is_sale_delivery_planning_applicable", "=", False),
            ("is_delivery_planned", "=", True),
        ]
        data = self.env["stock.picking"]._read_group(
            late_domain,
            ["picking_type_id"],
            ["picking_type_id"],
        )
        count = {
            row["picking_type_id"][0]: row["picking_type_id_count"]
            for row in data
            if row["picking_type_id"]
        }
        for record in self:
            record.count_picking_late = count.get(record.id, 0)
        return res

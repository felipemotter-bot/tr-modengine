# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import date, datetime

import pytz

from .common import DeliveryPlanningCommon


class TestInitFromCommitment(DeliveryPlanningCommon):
    def test_so_with_commitment_initializes_picking_planned_date(self):
        # commitment_date is a Datetime stored in UTC
        tz = pytz.timezone(self.env.user.tz or "UTC")
        local_dt = tz.localize(datetime(2026, 6, 20, 15, 0, 0))
        commitment_utc = local_dt.astimezone(pytz.utc).replace(tzinfo=None)
        _, picking = self._make_sale_order(commitment_date=commitment_utc)
        self.assertEqual(picking.planned_delivery_date, date(2026, 6, 20))

    def test_so_without_commitment_keeps_picking_planned_date_blank(self):
        _, picking = self._make_sale_order()
        self.assertFalse(picking.planned_delivery_date)

    def test_next_planned_delivery_date_reflects_picking(self):
        order, picking = self._make_sale_order()
        # Initially blank, no pending picking with planned date
        order.invalidate_recordset(["next_planned_delivery_date"])
        self.assertFalse(order.next_planned_delivery_date)
        # Planner sets a date
        picking.with_user(self.planner_user).write(
            {"planned_delivery_date": date(2026, 8, 10)}
        )
        order.invalidate_recordset(["next_planned_delivery_date"])
        self.assertEqual(order.next_planned_delivery_date, date(2026, 8, 10))

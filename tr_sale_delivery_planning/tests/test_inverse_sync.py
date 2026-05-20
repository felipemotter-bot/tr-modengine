# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import date

from .common import DeliveryPlanningCommon


class TestInverseSync(DeliveryPlanningCommon):
    def test_setting_planned_date_syncs_scheduled_date_end_of_day(self):
        _, picking = self._make_sale_order()
        target = date(2026, 6, 15)
        picking.with_user(self.planner_user).write({"planned_delivery_date": target})
        expected = self._expected_end_of_day_utc(target, self.planner_user)
        self.assertEqual(picking.scheduled_date, expected)

    def test_clearing_planned_date_does_not_reset_scheduled_date(self):
        _, picking = self._make_sale_order()
        target = date(2026, 6, 15)
        picking.with_user(self.planner_user).write({"planned_delivery_date": target})
        synced_value = picking.scheduled_date
        picking.with_user(self.planner_user).write({"planned_delivery_date": False})
        self.assertEqual(picking.scheduled_date, synced_value)

    def test_create_picking_with_planned_date_syncs_immediately(self):
        # Direct create as planner with planned_delivery_date in vals
        picking = (
            self.env["stock.picking"]
            .with_user(self.planner_user)
            .create(
                {
                    "picking_type_id": self.picking_type_out.id,
                    "location_id": self.location_internal.id,
                    "location_dest_id": self.location_customer.id,
                    "partner_id": self.partner.id,
                    "planned_delivery_date": date(2026, 7, 1),
                }
            )
        )
        expected = self._expected_end_of_day_utc(date(2026, 7, 1), self.planner_user)
        self.assertEqual(picking.scheduled_date, expected)

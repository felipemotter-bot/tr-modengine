# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import DeliveryPlanningCommon


class TestFieldBasics(DeliveryPlanningCommon):
    def test_new_fields_exist_on_picking(self):
        Picking = self.env["stock.picking"]
        self.assertIn("planned_delivery_date", Picking._fields)
        self.assertIn("is_delivery_planned", Picking._fields)
        self.assertIn("is_sale_delivery_planning_applicable", Picking._fields)

    def test_default_planned_delivery_date_is_blank(self):
        _, picking = self._make_sale_order()
        self.assertFalse(picking.planned_delivery_date)
        self.assertFalse(picking.is_delivery_planned)

    def test_is_delivery_planned_tracks_field(self):
        _, picking = self._make_sale_order()
        picking.with_user(self.planner_user).planned_delivery_date = "2026-06-15"
        self.assertTrue(picking.is_delivery_planned)
        picking.with_user(self.planner_user).planned_delivery_date = False
        self.assertFalse(picking.is_delivery_planned)

    def test_sale_order_next_planned_field_exists(self):
        self.assertIn("next_planned_delivery_date", self.env["sale.order"]._fields)

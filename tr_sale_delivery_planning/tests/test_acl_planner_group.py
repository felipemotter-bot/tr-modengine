# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import date

from odoo.exceptions import AccessError

from .common import DeliveryPlanningCommon


class TestAclPlannerGroup(DeliveryPlanningCommon):
    def test_non_planner_cannot_write_planned_delivery_date(self):
        _, picking = self._make_sale_order()
        with self.assertRaises(AccessError):
            picking.with_user(self.non_planner_user).write(
                {"planned_delivery_date": date(2026, 7, 5)}
            )

    def test_non_planner_cannot_create_picking_with_planned_date(self):
        with self.assertRaises(AccessError):
            self.env["stock.picking"].with_user(self.non_planner_user).create(
                {
                    "picking_type_id": self.picking_type_out.id,
                    "location_id": self.location_internal.id,
                    "location_dest_id": self.location_customer.id,
                    "partner_id": self.partner.id,
                    "planned_delivery_date": date(2026, 7, 5),
                }
            )

    def test_planner_can_write_planned_delivery_date(self):
        _, picking = self._make_sale_order()
        picking.with_user(self.planner_user).write(
            {"planned_delivery_date": date(2026, 7, 5)}
        )
        self.assertEqual(picking.planned_delivery_date, date(2026, 7, 5))

    def test_bypass_context_allows_write_without_group(self):
        _, picking = self._make_sale_order()
        # Simulate the SO confirmation path: bypass set, no planner group
        picking.with_user(self.non_planner_user).with_context(
            bypass_delivery_planning_acl=True
        ).write({"planned_delivery_date": date(2026, 7, 5)})
        self.assertEqual(picking.planned_delivery_date, date(2026, 7, 5))

    def test_so_confirmation_initializes_picking_without_user_being_planner(self):
        # Sale rep (non-planner) confirms a SO with commitment_date and
        # the init logic uses the bypass context internally.
        from datetime import datetime

        import pytz

        tz = pytz.timezone(self.env.user.tz or "UTC")
        local_dt = tz.localize(datetime(2026, 6, 22, 10, 0, 0))
        commitment_utc = local_dt.astimezone(pytz.utc).replace(tzinfo=None)
        order = (
            self.env["sale.order"]
            .with_user(self.non_planner_user)
            .create(
                {
                    "partner_id": self.partner.id,
                    "warehouse_id": self.warehouse.id,
                    "commitment_date": commitment_utc,
                    "order_line": [
                        (
                            0,
                            0,
                            {
                                "product_id": self.product.id,
                                "product_uom_qty": 1.0,
                            },
                        )
                    ],
                }
            )
        )
        order.action_confirm()
        picking = order.picking_ids.filtered(
            lambda p: p.picking_type_id.code == "outgoing"
        )
        self.assertEqual(picking.planned_delivery_date, date(2026, 6, 22))

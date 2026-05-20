# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import date, datetime, timedelta

import pytz

from .common import DeliveryPlanningCommon


class TestOverrides(DeliveryPlanningCommon):
    def test_has_deadline_issue_false_for_applicable_unplanned(self):
        # SO with commitment_date in the past creates pickings whose moves
        # carry date_deadline in the past — native code would mark
        # has_deadline_issue True; our override must clear it because
        # planned_delivery_date hasn't been set by the planner yet.
        tz = pytz.timezone(self.env.user.tz or "UTC")
        past = tz.localize(datetime(2025, 1, 1, 12, 0, 0))
        past_utc = past.astimezone(pytz.utc).replace(tzinfo=None)
        order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "warehouse_id": self.warehouse.id,
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
        # set commitment_date AFTER lines exist, then clear planned_date
        # to simulate "planner did not act yet"
        order.commitment_date = past_utc
        order.action_confirm()
        picking = order.picking_ids.filtered(
            lambda p: p.picking_type_id.code == "outgoing"
        )
        # The init logic copied commitment_date → planned_delivery_date.
        # Clear it to simulate the "planner hasn't acted" state.
        picking.with_user(self.planner_user).write({"planned_delivery_date": False})
        picking.invalidate_recordset(["has_deadline_issue"])
        self.assertFalse(picking.has_deadline_issue)

    def test_products_availability_cleared_for_applicable_unplanned(self):
        _, picking = self._make_sale_order()
        # planned_delivery_date is blank by default → applicable, unplanned
        picking.invalidate_recordset(
            ["products_availability", "products_availability_state"]
        )
        self.assertFalse(picking.products_availability_state)

    def test_purchase_picking_keeps_native_has_deadline_issue(self):
        # Purchase-style picking: not applicable. Override must NOT touch
        # has_deadline_issue (preserve native value, whatever it is).
        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": self.picking_type_in.id,
                "location_id": self.location_supplier.id,
                "location_dest_id": self.location_internal.id,
                "partner_id": self.partner.id,
            }
        )
        # Whatever native value is, our override must not override it
        native_value = picking.has_deadline_issue
        picking.invalidate_recordset(["has_deadline_issue"])
        self.assertEqual(picking.has_deadline_issue, native_value)

    def test_planned_picking_keeps_native_late_signal(self):
        # Deterministic: planned picking with date_deadline strictly less
        # than scheduled_date must keep has_deadline_issue=True (native
        # logic). Override must NOT clear it because picking is planned.
        _, picking = self._make_sale_order()
        # Planner sets a planned_delivery_date, which syncs scheduled_date
        # to end-of-day. Then we set date_deadline to a date strictly
        # earlier so native compute marks the issue.
        planned = date(2026, 9, 10)
        picking.with_user(self.planner_user).write({"planned_delivery_date": planned})
        # Force a date_deadline before scheduled_date.
        earlier = picking.scheduled_date - timedelta(days=5)
        picking.move_ids.write({"date_deadline": earlier})
        picking.invalidate_recordset(["has_deadline_issue"])
        self.assertTrue(picking.has_deadline_issue)

    def test_picking_type_count_late_excludes_applicable_unplanned(self):
        # Differential: capture count_picking_late baseline, then add an
        # applicable+unplanned late picking. The override must keep the
        # count unchanged. Then plan it (still in the past) and expect
        # the count to grow by 1 (now it IS counted as late).
        _, picking = self._make_sale_order()
        picking_type = picking.picking_type_id
        # Baseline before aging our picking
        picking_type.invalidate_recordset(["count_picking_late"])
        baseline = picking_type.count_picking_late
        # Age our picking — applicable, NOT planned
        past = datetime.utcnow() - timedelta(days=2)
        picking.scheduled_date = past
        picking_type.invalidate_recordset(["count_picking_late"])
        self.assertEqual(
            picking_type.count_picking_late,
            baseline,
            "Override must exclude sale-applicable unplanned pickings from "
            "count_picking_late",
        )
        # Now plan the picking with a past date (still late, but planned)
        picking.with_user(self.planner_user).write(
            {"planned_delivery_date": (datetime.utcnow() - timedelta(days=1)).date()}
        )
        picking_type.invalidate_recordset(["count_picking_late"])
        self.assertEqual(
            picking_type.count_picking_late,
            baseline + 1,
            "Once planned, the picking enters the native late count",
        )

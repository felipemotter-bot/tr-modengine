# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import datetime, time

import pytz

from odoo.tests.common import TransactionCase


class DeliveryPlanningCommon(TransactionCase):
    """Shared fixtures for the tr_sale_delivery_planning test suite."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.company = cls.env.ref("base.main_company")
        cls.planner_group = cls.env.ref(
            "tr_sale_delivery_planning.group_delivery_planner"
        )
        cls.warehouse = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.company.id)], limit=1
        )
        cls.picking_type_out = cls.warehouse.out_type_id
        cls.picking_type_in = cls.warehouse.in_type_id
        cls.picking_type_internal = cls.warehouse.int_type_id

        cls.location_internal = cls.warehouse.lot_stock_id
        cls.location_customer = cls.env.ref("stock.stock_location_customers")
        cls.location_supplier = cls.env.ref("stock.stock_location_suppliers")

        cls.partner = cls.env["res.partner"].create({"name": "Test Customer"})
        cls.product = cls.env["product.product"].create(
            {"name": "Test Product", "type": "product"}
        )

        cls.planner_user = cls.env["res.users"].create(
            {
                "name": "Planner User",
                "login": "planner@test.local",
                "email": "planner@test.local",
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            cls.env.ref("sales_team.group_sale_salesman").id,
                            cls.env.ref("stock.group_stock_user").id,
                            cls.planner_group.id,
                        ],
                    )
                ],
            }
        )
        cls.non_planner_user = cls.env["res.users"].create(
            {
                "name": "Non Planner User",
                "login": "nonplanner@test.local",
                "email": "nonplanner@test.local",
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            cls.env.ref("sales_team.group_sale_salesman").id,
                            cls.env.ref("stock.group_stock_user").id,
                        ],
                    )
                ],
            }
        )

    @classmethod
    def _make_sale_order(cls, commitment_date=None):
        """Create + confirm a sale order, return the SO + the outgoing picking."""
        order = cls.env["sale.order"].create(
            {
                "partner_id": cls.partner.id,
                "warehouse_id": cls.warehouse.id,
                "commitment_date": commitment_date,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": cls.product.id,
                            "product_uom_qty": 1.0,
                        },
                    )
                ],
            }
        )
        order.action_confirm()
        picking = order.picking_ids.filtered(
            lambda p: p.picking_type_id.code == "outgoing"
        )
        return order, picking

    @staticmethod
    def _expected_end_of_day_utc(date_value, user):
        tz = pytz.timezone(user.tz or "UTC")
        local_dt = tz.localize(datetime.combine(date_value, time(23, 59, 59)))
        return local_dt.astimezone(pytz.utc).replace(tzinfo=None)

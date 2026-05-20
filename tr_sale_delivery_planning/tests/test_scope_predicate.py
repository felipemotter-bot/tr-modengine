# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import DeliveryPlanningCommon


class TestScopePredicate(DeliveryPlanningCommon):
    def test_outgoing_sale_picking_is_applicable(self):
        _, picking = self._make_sale_order()
        self.assertTrue(picking.is_sale_delivery_planning_applicable)

    def test_purchase_incoming_picking_is_not_applicable(self):
        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": self.picking_type_in.id,
                "location_id": self.location_supplier.id,
                "location_dest_id": self.location_internal.id,
                "partner_id": self.partner.id,
            }
        )
        self.assertFalse(picking.is_sale_delivery_planning_applicable)

    def test_internal_transfer_is_not_applicable(self):
        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": self.picking_type_internal.id,
                "location_id": self.location_internal.id,
                "location_dest_id": self.location_internal.id,
            }
        )
        self.assertFalse(picking.is_sale_delivery_planning_applicable)

    def test_outgoing_picking_without_sale_is_not_applicable(self):
        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": self.picking_type_out.id,
                "location_id": self.location_internal.id,
                "location_dest_id": self.location_customer.id,
                "partner_id": self.partner.id,
            }
        )
        # Manually-created outgoing picking with no sale_id link
        self.assertFalse(picking.is_sale_delivery_planning_applicable)

    def test_customer_return_is_not_applicable(self):
        """A return shipment goes customer -> internal, not internal ->
        customer. Even with a sale_id, the predicate must reject it."""
        _, original_picking = self._make_sale_order()
        # Simulate the return type by manually building one with reversed
        # locations and the same sale-linked procurement group.
        return_picking = self.env["stock.picking"].create(
            {
                "picking_type_id": self.picking_type_out.id,
                "location_id": self.location_customer.id,
                "location_dest_id": self.location_internal.id,
                "partner_id": self.partner.id,
                "group_id": original_picking.group_id.id,
            }
        )
        self.assertFalse(return_picking.is_sale_delivery_planning_applicable)

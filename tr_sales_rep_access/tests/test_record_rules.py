# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError

from .common import SalesRepAccessTestCommon


class TestRecordRules(SalesRepAccessTestCommon):
    def test_partner_visibility_by_agent(self):
        """U1 sees C1 (agent A1), not C2 (agent A2), not C3 (no agent)."""
        Partner = self.env["res.partner"].with_user(self.user_u1)
        ids = Partner.search([]).ids
        self.assertIn(self.customer_c1.id, ids)
        self.assertNotIn(self.customer_c2.id, ids)
        self.assertNotIn(self.customer_c3.id, ids)

    def test_partner_sees_own_user_partner(self):
        """U1 sees its own partner (exception in the rule)."""
        Partner = self.env["res.partner"].with_user(self.user_u1)
        self.assertIn(self.agent_a1.id, Partner.search([]).ids)

    def test_partner_sees_commercial_children(self):
        """Children of C1 inherit commercial_partner_id and are visible."""
        child = self.env["res.partner"].create(
            {
                "name": "C1 Shipping",
                "parent_id": self.customer_c1.id,
                "type": "delivery",
            }
        )
        Partner = self.env["res.partner"].with_user(self.user_u1)
        self.assertIn(child.id, Partner.search([]).ids)

    def test_sale_order_visibility_read(self):
        """U1 reads orders of C1 (including confirmed); not of C2 or C3."""
        order_c1 = self._make_order(self.customer_c1)
        order_c2 = self._make_order(self.customer_c2)
        order_c3 = self._make_order(self.customer_c3)

        SaleOrder = self.env["sale.order"].with_user(self.user_u1)
        visible_ids = SaleOrder.search([]).ids
        self.assertIn(order_c1.id, visible_ids)
        self.assertNotIn(order_c2.id, visible_ids)
        self.assertNotIn(
            order_c3.id,
            visible_ids,
            "Order with sales_rep_partner_id NULL must NOT be visible.",
        )

    def test_sale_order_write_blocked_after_draft(self):
        """U1 can write in draft; cannot write after confirm."""
        order = self._make_order(self.customer_c1)
        # Still in draft — write is allowed.
        order.with_user(self.user_u1).write({"client_order_ref": "OC-1"})
        self.assertEqual(order.client_order_ref, "OC-1")

        # Confirm as admin (rep does not confirm in the business flow).
        order.action_confirm()
        with self.assertRaises(AccessError):
            order.with_user(self.user_u1).write({"client_order_ref": "OC-2"})

    def test_sale_order_line_visibility(self):
        """U1 sees only lines from C1 orders."""
        order_c1 = self._make_order(self.customer_c1)
        order_c2 = self._make_order(self.customer_c2)

        Line = self.env["sale.order.line"].with_user(self.user_u1)
        visible_ids = Line.search([]).ids
        for line in order_c1.order_line:
            self.assertIn(line.id, visible_ids)
        for line in order_c2.order_line:
            self.assertNotIn(line.id, visible_ids)

    def test_account_move_visibility_by_snapshot(self):
        """U1 sees invoice with snapshot=A1; not A2.

        account.move records are created directly (not via
        ``_create_invoices``) because the Brazilian localization
        requires fiscal_operation_id/line which are out of scope
        for this foundation PR.
        """
        invoice_c1 = self._make_invoice(self.customer_c1, self.agent_a1)
        invoice_c2 = self._make_invoice(self.customer_c2, self.agent_a2)

        Move = self.env["account.move"].with_user(self.user_u1)
        visible_ids = Move.search([]).ids
        self.assertIn(invoice_c1.id, visible_ids)
        self.assertNotIn(invoice_c2.id, visible_ids)

    def test_account_move_line_visibility_by_move_snapshot(self):
        """U1 sees move lines of invoices in scope only."""
        invoice_c1 = self._make_invoice(self.customer_c1, self.agent_a1)
        invoice_c2 = self._make_invoice(self.customer_c2, self.agent_a2)

        Line = self.env["account.move.line"].with_user(self.user_u1)
        visible_ids = Line.search([]).ids
        for line in invoice_c1.line_ids:
            self.assertIn(line.id, visible_ids)
        for line in invoice_c2.line_ids:
            self.assertNotIn(line.id, visible_ids)

    def test_partner_visible_after_agent_rotation_via_sale_snapshot(self):
        """Rule clause for historical sale snapshots.

        A1 owns an order on C1 (snapshot = A1). Customer's agent is
        later rotated to A2. The live ``agent_ids`` clause no longer
        matches for A1, but the historical-snapshot clause keeps the
        partner readable so A1 can still open and print the old order.
        """
        self._make_order(self.customer_c1)
        # Rotate the customer's agent to A2 (live clause now excludes A1).
        self.customer_c1.agent_ids = [(6, 0, [self.agent_a2.id])]

        Partner = self.env["res.partner"].with_user(self.user_u1)
        self.assertIn(self.customer_c1.id, Partner.search([]).ids)
        # Field-level read still succeeds (template path).
        self.customer_c1.with_user(self.user_u1).read(["name", "vat"])

    def test_partner_child_visible_after_agent_rotation(self):
        """Shipping/invoice children inherit commercial_partner_id and
        ride on the same historical clause."""
        child = self.env["res.partner"].create(
            {
                "name": "C1 Shipping",
                "parent_id": self.customer_c1.id,
                "type": "delivery",
            }
        )
        self._make_order(self.customer_c1)
        self.customer_c1.agent_ids = [(6, 0, [self.agent_a2.id])]

        Partner = self.env["res.partner"].with_user(self.user_u1)
        self.assertIn(child.id, Partner.search([]).ids)

    def test_partner_visible_after_agent_rotation_via_invoice_snapshot(self):
        """Same clause covers account.move snapshots."""
        self._make_invoice(self.customer_c1, self.agent_a1)
        self.customer_c1.agent_ids = [(6, 0, [self.agent_a2.id])]

        Partner = self.env["res.partner"].with_user(self.user_u1)
        self.assertIn(self.customer_c1.id, Partner.search([]).ids)

    def test_partner_not_visible_without_any_snapshot(self):
        """No snapshot, no live agent: partner stays hidden.

        Guards against the historical clause accidentally broadening
        visibility to customers the rep never dealt with.
        """
        Partner = self.env["res.partner"].with_user(self.user_u1)
        # C2 is A2's customer, U1 never owned any sale/move on it.
        self.assertNotIn(self.customer_c2.id, Partner.search([]).ids)
        # C3 has no agent at all.
        self.assertNotIn(self.customer_c3.id, Partner.search([]).ids)

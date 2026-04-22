# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import SalesRepAccessTestCommon


@tagged("post_install", "-at_install")
class TestRepOrderFlow(SalesRepAccessTestCommon):
    """End-to-end integration tests for a rep building a sale order.

    Focuses on the commission-agent path: the rep has no ACL on
    ``sale.order.line.agent`` but the line compute must still be
    able to populate it via the sudo override. Direct RPC access to
    the child model must remain blocked.
    """

    def test_rep_creates_order_and_agent_is_populated(self):
        """Rep U1 creates an order with a line; _compute_agent_ids
        runs under sudo and populates the agent line without
        raising AccessError under the rep's session.
        """
        order = (
            self.env["sale.order"]
            .with_user(self.user_u1)
            .create(
                {
                    "partner_id": self.customer_c1.id,
                    "order_line": [
                        (
                            0,
                            0,
                            {
                                "product_id": self.product.id,
                                "product_uom_qty": 1.0,
                            },
                        ),
                    ],
                }
            )
        )
        self.assertEqual(order.state, "draft")
        self.assertEqual(len(order.order_line), 1)
        # Read child model via sudo (rep cannot directly).
        agents = order.order_line.sudo().agent_ids
        self.assertTrue(
            agents,
            "agent_ids must be populated by the sudo'd compute on rep create",
        )
        self.assertEqual(agents.agent_id, self.agent_a1)

    def test_rep_cannot_read_sale_order_line_agent_directly(self):
        """No ACL on sale.order.line.agent → any direct RPC read fails."""
        with self.assertRaises(AccessError):
            self.env["sale.order.line.agent"].with_user(self.user_u1).search([])

    def test_rep_cannot_create_sale_order_line_agent_directly(self):
        """Direct RPC create on sale.order.line.agent is blocked."""
        with self.assertRaises(AccessError):
            self.env["sale.order.line.agent"].with_user(self.user_u1).create(
                {
                    "object_id": 1,
                    "agent_id": self.agent_a1.id,
                    "commission_id": self.commission.id,
                }
            )

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import SalesRepAccessTestCommon


@tagged("post_install", "-at_install")
class TestRepCommissionBlock(SalesRepAccessTestCommon):
    """Rep cannot manipulate commission_id on sale.order.line.agent.

    Regression tests for the ORM-level ACL block on sale.order.line.agent.
    The rep group has no access to this model at all, so any attempt —
    direct or via nested One2many commands on sale.order.line — must
    raise AccessError. Ensures the rep cannot set a non-managed
    commission to bypass the commercial policy bands.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.order = cls.env["sale.order"].create(
            {
                "partner_id": cls.customer_c1.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": cls.product.id,
                            "product_uom_qty": 1.0,
                            "price_unit": 100.0,
                        },
                    ),
                ],
            }
        )
        cls.line = cls.order.order_line[:1]
        cls.line_agent = cls.line.agent_ids[:1]

    def test_rep_cannot_write_commission_id_on_line_agent(self):
        """Direct RPC write to sale.order.line.agent is blocked."""
        fake_commission = self.env["commission"].sudo().search([], limit=1)
        with self.assertRaises(AccessError):
            self.line_agent.with_user(self.user_u1).write(
                {"commission_id": fake_commission.id}
            )

    def test_rep_cannot_create_line_agent_directly(self):
        """Direct create on sale.order.line.agent is blocked."""
        fake_commission = self.env["commission"].sudo().search([], limit=1)
        with self.assertRaises(AccessError):
            self.env["sale.order.line.agent"].with_user(self.user_u1).create(
                {
                    "object_id": self.line.id,
                    "agent_id": self.agent_a1.id,
                    "commission_id": fake_commission.id,
                }
            )

    def test_rep_cannot_unlink_line_agent(self):
        """Direct unlink on sale.order.line.agent is blocked."""
        with self.assertRaises(AccessError):
            self.line_agent.with_user(self.user_u1).unlink()

    def test_rep_cannot_nested_write_agent_ids_on_line(self):
        """Nested (1, id, vals) write via sale.order.line is blocked.

        sale.order.line.write({'agent_ids': [(1, id, {...})]}) routes
        through the child model's write, which still checks the rep's
        ACL on sale.order.line.agent.
        """
        fake_commission = self.env["commission"].sudo().search([], limit=1)
        with self.assertRaises(AccessError):
            self.line.with_user(self.user_u1).write(
                {
                    "agent_ids": [
                        (1, self.line_agent.id, {"commission_id": fake_commission.id})
                    ]
                }
            )

    def test_rep_cannot_nested_create_agent_ids_on_line(self):
        """Nested (0, 0, vals) create via sale.order.line is blocked."""
        fake_commission = self.env["commission"].sudo().search([], limit=1)
        with self.assertRaises(AccessError):
            self.line.with_user(self.user_u1).write(
                {
                    "agent_ids": [
                        (
                            0,
                            0,
                            {
                                "agent_id": self.agent_a1.id,
                                "commission_id": fake_commission.id,
                            },
                        )
                    ]
                }
            )

    def test_rep_cannot_create_order_with_agent_ids_in_line_vals(self):
        """Create sale.order with nested agent_ids in line vals is blocked.

        Prevents bypass at order-creation time via the double-nested
        sale.order → order_line → agent_ids path.
        """
        fake_commission = self.env["commission"].sudo().search([], limit=1)
        with self.assertRaises(AccessError):
            self.env["sale.order"].with_user(self.user_u1).create(
                {
                    "partner_id": self.customer_c1.id,
                    "order_line": [
                        (
                            0,
                            0,
                            {
                                "product_id": self.product.id,
                                "product_uom_qty": 1.0,
                                "price_unit": 100.0,
                                "agent_ids": [
                                    (
                                        0,
                                        0,
                                        {
                                            "agent_id": self.agent_a1.id,
                                            "commission_id": fake_commission.id,
                                        },
                                    )
                                ],
                            },
                        )
                    ],
                }
            )

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestCommissionPostConfirmGuard(CommercialPolicyTestCommon):
    """State-based freeze on sale.order.line.agent structure.

    Once the parent sale.order is past draft/sent, no user except a
    sales director (or system admin) may create, write or unlink an
    agent line. Defense in depth on top of _check_stale_commissions
    which only runs at confirm time.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

        # System-only user: has base.group_system but NOT group_sales_director.
        # Used to verify the admin-bypass branch of _has_post_confirm_bypass.
        cls.admin_only_user = cls.env["res.users"].create(
            {
                "name": "Admin Only (system, not director)",
                "login": "test_admin_only_guard",
                "groups_id": [
                    (4, cls.env.ref("sales_team.group_sale_salesman").id),
                    (4, cls.env.ref("base.group_system").id),
                ],
            }
        )

        # A second valid managed commission the director can swap to, so
        # the director-bypass test shows *actual* mutation rather than a
        # no-op write.
        cls.commission_alt = cls.env["commission"].create(
            {
                "name": "Alt Commission",
                "commission_type": "fixed",
                "fix_qty": 5.0,
            }
        )

    def _make_confirmed_order_with_agent_line(self, seller_discount=3.0):
        """Create and confirm an order that has at least one agent line."""
        self.product_template_a.invoice_policy = "order"
        order = self._create_order()
        order.fiscal_operation_id = False
        line = self._create_order_line(order, qty=10)
        line.seller_discount = seller_discount
        line.extra_discount = 0.0
        order.action_confirm()
        agent_line = line.agent_ids.filtered(lambda a: a.agent_id == self.agent_partner)
        self.assertTrue(agent_line, "Setup failure: no agent line on confirmed order")
        return order, line, agent_line

    # ------------------------------------------------------------------
    # Salesman blocked post-confirm
    # ------------------------------------------------------------------

    def test_salesman_blocked_write_post_confirm(self):
        _order, _line, agent_line = self._make_confirmed_order_with_agent_line()
        with self.assertRaises(UserError):
            agent_line.with_user(self.salesperson).write(
                {"commission_id": self.commission_alt.id}
            )

    def test_salesman_blocked_create_post_confirm(self):
        order, line, _agent_line = self._make_confirmed_order_with_agent_line()
        new_agent = self.env["res.partner"].create(
            {
                "name": "Extra Agent",
                "agent": True,
                "commission_id": self.commission.id,
                "sales_profile_id": self.agent_profile.id,
            }
        )
        with self.assertRaises(UserError):
            self.env["sale.order.line.agent"].with_user(self.salesperson).create(
                {
                    "object_id": line.id,
                    "agent_id": new_agent.id,
                    "commission_id": self.commission.id,
                }
            )

    def test_salesman_blocked_unlink_post_confirm(self):
        _order, _line, agent_line = self._make_confirmed_order_with_agent_line()
        with self.assertRaises(UserError):
            agent_line.with_user(self.salesperson).unlink()

    def test_salesman_blocked_agent_id_change_post_confirm(self):
        """Changing agent_id alone (without commission_id) is also frozen."""
        _order, _line, agent_line = self._make_confirmed_order_with_agent_line()
        other_agent = self.env["res.partner"].create(
            {
                "name": "Other Agent",
                "agent": True,
                "commission_id": self.commission.id,
                "sales_profile_id": self.agent_profile.id,
            }
        )
        with self.assertRaises(UserError):
            agent_line.with_user(self.salesperson).write({"agent_id": other_agent.id})

    # ------------------------------------------------------------------
    # Director bypass
    # ------------------------------------------------------------------

    def test_director_allowed_write_post_confirm(self):
        _order, _line, agent_line = self._make_confirmed_order_with_agent_line()
        agent_line.with_user(self.director_user).write(
            {"commission_id": self.commission_alt.id}
        )
        self.assertEqual(agent_line.commission_id, self.commission_alt)

    def test_director_allowed_unlink_post_confirm(self):
        _order, _line, agent_line = self._make_confirmed_order_with_agent_line()
        agent_id = agent_line.id
        agent_line.with_user(self.director_user).unlink()
        self.assertFalse(self.env["sale.order.line.agent"].browse(agent_id).exists())

    # ------------------------------------------------------------------
    # Draft path still works
    # ------------------------------------------------------------------

    def test_salesman_write_allowed_in_draft(self):
        order = self._create_order()
        line = self._create_order_line(order, qty=10)
        line.seller_discount = 3.0
        line.extra_discount = 0.0
        agent_line = line.agent_ids.filtered(lambda a: a.agent_id == self.agent_partner)
        self.assertEqual(order.state, "draft")
        agent_line.with_user(self.salesperson).write(
            {"commission_id": self.commission_alt.id}
        )
        self.assertEqual(agent_line.commission_id, self.commission_alt)

    def test_resolve_agent_commissions_updates_commission_id_in_draft(self):
        """Changing seller_discount in draft re-resolves commission via
        _resolve_agent_commissions. The write of the new managed
        commission_id must not trip the guard.
        """
        order = self._create_order()
        line = self._create_order_line(order, qty=10)
        line.seller_discount = 3.0  # falls in band_low → rate 10%
        line.extra_discount = 0.0
        agent_line = line.agent_ids.filtered(lambda a: a.agent_id == self.agent_partner)
        initial_commission = agent_line.commission_id

        line.seller_discount = 7.0  # falls in band_high → rate 7%
        new_commission = agent_line.commission_id

        self.assertNotEqual(
            initial_commission,
            new_commission,
            "Band change must actually swap the managed commission for the "
            "test to be meaningful.",
        )
        self.assertTrue(new_commission.tr_managed)

    # ------------------------------------------------------------------
    # Non-editable states all frozen
    # ------------------------------------------------------------------

    def test_cancel_state_still_blocked(self):
        _order, _line, agent_line = self._make_confirmed_order_with_agent_line()
        _order._action_cancel()
        self.assertEqual(_order.state, "cancel")
        with self.assertRaises(UserError):
            agent_line.with_user(self.salesperson).write(
                {"commission_id": self.commission_alt.id}
            )

    # ------------------------------------------------------------------
    # System admin bypass
    # ------------------------------------------------------------------

    def test_admin_bypass_works(self):
        _order, _line, agent_line = self._make_confirmed_order_with_agent_line()
        agent_line.with_user(self.admin_only_user).write(
            {"commission_id": self.commission_alt.id}
        )
        self.assertEqual(agent_line.commission_id, self.commission_alt)

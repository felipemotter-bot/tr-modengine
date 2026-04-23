# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestInvoiceCommissionGuard(CommercialPolicyTestCommon):
    """State-based freeze on account.invoice.line.agent structure.

    Mirrors TestCommissionPostConfirmGuard (PR #34, sale side) but
    for invoices. Once the invoice is past ``draft`` (posted or
    cancelled), only a sales director (or system admin) may
    create, write or unlink an agent line.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

        # account.invoice.line.agent requires ``account.group_account_invoice``.
        # Common test users only have sales-team groups, so extend the two
        # users we exercise on the invoice side.
        invoice_group = cls.env.ref("account.group_account_invoice")
        cls.salesperson.write({"groups_id": [(4, invoice_group.id)]})
        cls.director_user.write({"groups_id": [(4, invoice_group.id)]})

        cls.admin_only_user = cls.env["res.users"].create(
            {
                "name": "Admin Only (system, not director)",
                "login": "test_invoice_guard_admin_only",
                "groups_id": [
                    (4, cls.env.ref("sales_team.group_sale_salesman").id),
                    (4, invoice_group.id),
                    (4, cls.env.ref("base.group_system").id),
                ],
            }
        )

        cls.commission_alt = cls.env["commission"].create(
            {
                "name": "Alt Commission (invoice guard)",
                "commission_type": "fixed",
                "fix_qty": 5.0,
            }
        )

    def _make_posted_invoice_with_agent_line(self, seller_discount=3.0):
        """Confirm a rep order and generate + post the invoice so we
        have an agent line on a move past draft."""
        order, invoice = self._create_confirmed_order_with_invoice(
            seller_discount=seller_discount
        )
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")
        agent_line = invoice.invoice_line_ids.agent_ids.filtered(
            lambda a: a.agent_id == self.agent_partner
        )
        self.assertTrue(agent_line, "Setup failure: no agent line on posted invoice")
        return invoice, agent_line

    def _make_settled_agent_line(self):
        """Build a posted invoice with a settled agent line by
        attaching it to a ``settled`` commission.settlement."""
        invoice, agent_line = self._make_posted_invoice_with_agent_line()
        settlement = self.env["commission.settlement"].create(
            {
                "agent_id": self.agent_partner.id,
                "date_from": "2026-01-01",
                "date_to": "2026-12-31",
                "state": "settled",
            }
        )
        self.env["commission.settlement.line"].create(
            {
                "settlement_id": settlement.id,
                "invoice_agent_line_id": agent_line.id,
                "commission_id": agent_line.commission_id.id,
                "date": "2026-06-01",
            }
        )
        self.assertTrue(
            agent_line.settlement_line_ids, "Setup failure: line not settled"
        )
        return invoice, agent_line

    # ------------------------------------------------------------------
    # Salesman blocked once the invoice is past draft
    # ------------------------------------------------------------------

    def test_salesman_blocked_write_post_invoice_post(self):
        _invoice, agent_line = self._make_posted_invoice_with_agent_line()
        with self.assertRaises(UserError):
            agent_line.with_user(self.salesperson).write(
                {"commission_id": self.commission_alt.id}
            )

    def test_salesman_blocked_create_post_invoice_post(self):
        invoice, _agent_line = self._make_posted_invoice_with_agent_line()
        line = invoice.invoice_line_ids[:1]
        new_agent = self.env["res.partner"].create(
            {
                "name": "Extra Agent (invoice guard)",
                "agent": True,
                "commission_id": self.commission.id,
                "sales_profile_id": self.agent_profile.id,
            }
        )
        with self.assertRaises(UserError):
            self.env["account.invoice.line.agent"].with_user(self.salesperson).create(
                {
                    "object_id": line.id,
                    "agent_id": new_agent.id,
                    "commission_id": self.commission.id,
                }
            )

    def test_salesman_blocked_unlink_post_invoice_post(self):
        _invoice, agent_line = self._make_posted_invoice_with_agent_line()
        with self.assertRaises(UserError):
            agent_line.with_user(self.salesperson).unlink()

    def test_salesman_blocked_agent_id_change_post_invoice_post(self):
        _invoice, agent_line = self._make_posted_invoice_with_agent_line()
        other_agent = self.env["res.partner"].create(
            {
                "name": "Other Agent (invoice guard)",
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

    def test_director_allowed_write_post_invoice_post(self):
        _invoice, agent_line = self._make_posted_invoice_with_agent_line()
        agent_line.with_user(self.director_user).write(
            {"commission_id": self.commission_alt.id}
        )
        self.assertEqual(agent_line.commission_id, self.commission_alt)

    def test_director_allowed_unlink_post_invoice_post(self):
        _invoice, agent_line = self._make_posted_invoice_with_agent_line()
        agent_id = agent_line.id
        agent_line.with_user(self.director_user).unlink()
        self.assertFalse(
            self.env["account.invoice.line.agent"].browse(agent_id).exists()
        )

    # ------------------------------------------------------------------
    # Draft stays open
    # ------------------------------------------------------------------

    def test_salesman_write_allowed_in_draft_invoice(self):
        order, invoice = self._create_confirmed_order_with_invoice(seller_discount=3.0)
        self.assertEqual(invoice.state, "draft")
        agent_line = invoice.invoice_line_ids.agent_ids.filtered(
            lambda a: a.agent_id == self.agent_partner
        )
        self.assertTrue(agent_line)
        agent_line.with_user(self.salesperson).write(
            {"commission_id": self.commission_alt.id}
        )
        self.assertEqual(agent_line.commission_id, self.commission_alt)

    # ------------------------------------------------------------------
    # Cancelled move is also frozen
    # ------------------------------------------------------------------

    def test_cancelled_invoice_still_blocked(self):
        invoice, agent_line = self._make_posted_invoice_with_agent_line()
        invoice.button_cancel()
        self.assertEqual(invoice.state, "cancel")
        with self.assertRaises(UserError):
            agent_line.with_user(self.salesperson).write(
                {"commission_id": self.commission_alt.id}
            )

    # ------------------------------------------------------------------
    # System admin bypass
    # ------------------------------------------------------------------

    def test_admin_bypass_works_on_invoice(self):
        _invoice, agent_line = self._make_posted_invoice_with_agent_line()
        agent_line.with_user(self.admin_only_user).write(
            {"commission_id": self.commission_alt.id}
        )
        self.assertEqual(agent_line.commission_id, self.commission_alt)

    # ------------------------------------------------------------------
    # Settled lines are frozen for unlink even for directors
    # ------------------------------------------------------------------

    def test_director_blocked_unlink_settled_line(self):
        _invoice, agent_line = self._make_settled_agent_line()
        with self.assertRaises(UserError):
            agent_line.with_user(self.director_user).unlink()

    def test_admin_allowed_unlink_settled_line(self):
        _invoice, agent_line = self._make_settled_agent_line()
        agent_id = agent_line.id
        agent_line.with_user(self.admin_only_user).unlink()
        self.assertFalse(
            self.env["account.invoice.line.agent"].browse(agent_id).exists()
        )

    def test_director_blocked_write_settled_line(self):
        _invoice, agent_line = self._make_settled_agent_line()
        with self.assertRaises(UserError):
            agent_line.with_user(self.director_user).write(
                {"commission_id": self.commission_alt.id}
            )

    def test_admin_allowed_write_settled_line(self):
        _invoice, agent_line = self._make_settled_agent_line()
        agent_line.with_user(self.admin_only_user).write(
            {"commission_id": self.commission_alt.id}
        )
        self.assertEqual(agent_line.commission_id, self.commission_alt)

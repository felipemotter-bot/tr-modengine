# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import SalesRepAccessTestCommon

_CHATTER_FIELDS = ("message_ids", "message_follower_ids")


@tagged("post_install", "-at_install")
class TestRepChatter(SalesRepAccessTestCommon):
    """PR 8 — chatter hidden for external reps.

    Strategy: field-level groups= closes the RPC read path
    (fields_get / read).  write() override with mail_notrack=True
    prevents tracking noise when the rep edits records.
    mail.activity is preserved (separate model, unaffected).

    Note: mail.message / mail.followers ACLs are NOT restricted
    globally so that mail.activity creation (which internally calls
    message_subscribe) continues to work.  The accepted residual risk
    is documented in AGENTS.md.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Create as admin so that recompute cycles (e.g.
        # _compute_condition_discounts) do not pass through the rep write
        # override and cause flush_all() issues in subsequent test setUps.
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

    def test_rep_cannot_read_message_ids_on_sale_order(self):
        # With groups= on message_ids, the ORM raises AccessError when
        # the rep calls read([field]) explicitly.
        for fname in _CHATTER_FIELDS:
            with self.assertRaises(
                AccessError,
                msg=f"Rep must not be able to read {fname} on sale.order",
            ):
                self.order.with_user(self.user_u1).read([fname])

    def test_rep_message_ids_not_in_fields_get_sale_order(self):
        info = self.env["sale.order"].with_user(self.user_u1).fields_get()
        leaked = [f for f in _CHATTER_FIELDS if f in info]
        self.assertFalse(
            leaked,
            f"fields_get must not expose chatter fields for rep, leaked: {leaked}",
        )

    def test_admin_can_read_message_ids_on_sale_order(self):
        # Regression: admin must not be affected by the rep restriction.
        values = self.order.sudo().read(list(_CHATTER_FIELDS))
        self.assertEqual(len(values), 1)
        self.assertIn("message_ids", values[0])

    def test_rep_write_does_not_create_tracking_on_sale_order(self):
        # mail_notrack=True in write() suppresses tracking messages.
        # Rep writes tr_rep_notes (a plain text field) and the
        # message count in the chatter must not increase.
        msg_before = self.order.sudo().message_ids
        self.order.with_user(self.user_u1).write({"tr_rep_notes": "test note"})
        msg_after = self.order.sudo().message_ids
        self.assertEqual(
            len(msg_after),
            len(msg_before),
            "Rep write must not produce tracking messages in the chatter",
        )

    def test_rep_cannot_read_message_ids_on_partner(self):
        for fname in _CHATTER_FIELDS:
            with self.assertRaises(
                AccessError,
                msg=f"Rep must not be able to read {fname} on res.partner",
            ):
                self.customer_c1.with_user(self.user_u1).read([fname])

    def test_rep_message_ids_not_in_fields_get_partner(self):
        info = self.env["res.partner"].with_user(self.user_u1).fields_get()
        leaked = [f for f in _CHATTER_FIELDS if f in info]
        self.assertFalse(
            leaked,
            f"fields_get must not expose chatter fields for rep on partner, leaked: {leaked}",
        )

    def test_rep_cannot_read_message_ids_on_account_move(self):
        invoice = self._make_invoice(self.customer_c1, rep_agent=self.agent_a1)
        for fname in _CHATTER_FIELDS:
            with self.assertRaises(
                AccessError,
                msg=f"Rep must not be able to read {fname} on account.move",
            ):
                invoice.with_user(self.user_u1).read([fname])

    def test_rep_message_ids_not_in_fields_get_account_move(self):
        info = self.env["account.move"].with_user(self.user_u1).fields_get()
        leaked = [f for f in _CHATTER_FIELDS if f in info]
        self.assertFalse(
            leaked,
            f"fields_get must not expose chatter fields for rep on account.move, leaked: {leaked}",
        )

    def test_chatter_div_absent_from_sale_order_form_for_rep(self):
        arch = (
            self.env["sale.order"]
            .with_user(self.user_u1)
            .fields_view_get(view_type="form")["arch"]
        )
        self.assertNotIn(
            "oe_chatter",
            arch,
            "oe_chatter div must be stripped from sale.order form arch for rep",
        )

    def test_chatter_div_present_in_sale_order_form_for_admin(self):
        arch = self.env["sale.order"].sudo().fields_view_get(view_type="form")["arch"]
        self.assertIn(
            "oe_chatter",
            arch,
            "oe_chatter div must remain in sale.order form arch for admin",
        )

    def test_chatter_div_absent_from_partner_form_for_rep(self):
        arch = (
            self.env["res.partner"]
            .with_user(self.user_u1)
            .fields_view_get(view_type="form")["arch"]
        )
        self.assertNotIn(
            "oe_chatter",
            arch,
            "oe_chatter div must be stripped from res.partner form arch for rep",
        )

    def test_chatter_div_absent_from_account_move_form_for_rep(self):
        arch = (
            self.env["account.move"]
            .with_user(self.user_u1)
            .fields_view_get(view_type="form")["arch"]
        )
        self.assertNotIn(
            "oe_chatter",
            arch,
            "oe_chatter div must be stripped from account.move form arch for rep",
        )

    def test_rep_can_create_activity(self):
        # mail.activity is a separate model and must remain functional
        # for reps — they need to schedule follow-ups on their orders.
        activity_type = self.env.ref("mail.mail_activity_data_todo")
        activity = (
            self.env["mail.activity"]
            .with_user(self.user_u1)
            .create(
                {
                    "res_model_id": self.env["ir.model"]._get("sale.order").id,
                    "res_id": self.order.id,
                    "activity_type_id": activity_type.id,
                    "summary": "Follow up",
                }
            )
        )
        self.assertTrue(activity.exists(), "Rep must be able to create activities")
        activity.sudo().unlink()

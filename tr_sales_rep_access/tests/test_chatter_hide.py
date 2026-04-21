# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import SalesRepAccessTestCommon

_MAIL_THREAD_AUX_FIELDS = (
    "message_is_follower",
    "message_partner_ids",
    "has_message",
    "message_needaction",
    "message_needaction_counter",
    "message_has_error",
    "message_has_error_counter",
    "message_attachment_count",
    "message_main_attachment_id",
)

# Fields from portal.mixin — only on sale.order and account.move.
_PORTAL_FIELDS = ("website_message_ids",)


@tagged("post_install", "-at_install")
class TestChatterHide(SalesRepAccessTestCommon):
    """PR 12 — auxiliary mail.thread fields invisible for external reps.

    PR 8 blocked message_ids and message_follower_ids. PR 12 extends the
    block to all auxiliary fields (follower list, attachment count,
    needaction counters, etc.) so that metadata cannot be read via
    direct RPC read / fields_get either.
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
        cls.invoice = cls.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": cls.customer_c1.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": cls.product.id,
                            "quantity": 1.0,
                            "price_unit": 100.0,
                        },
                    ),
                ],
            }
        )
        cls.invoice.sales_rep_partner_id = cls.agent_a1.id

    # ------------------------------------------------------------------
    # sale.order
    # ------------------------------------------------------------------

    def test_rep_aux_mail_fields_not_in_fields_get_sale_order(self):
        info = self.env["sale.order"].with_user(self.user_u1).fields_get()
        leaked = [f for f in _MAIL_THREAD_AUX_FIELDS + _PORTAL_FIELDS if f in info]
        self.assertFalse(leaked, f"fields_get must not expose {leaked} on sale.order")

    def test_rep_cannot_read_message_partner_ids_on_sale_order(self):
        with self.assertRaises(AccessError):
            self.order.with_user(self.user_u1).read(["message_partner_ids"])

    def test_rep_cannot_read_message_attachment_count_on_sale_order(self):
        with self.assertRaises(AccessError):
            self.order.with_user(self.user_u1).read(["message_attachment_count"])

    def test_rep_cannot_read_website_message_ids_on_sale_order(self):
        with self.assertRaises(AccessError):
            self.order.with_user(self.user_u1).read(["website_message_ids"])

    # ------------------------------------------------------------------
    # account.move
    # ------------------------------------------------------------------

    def test_rep_aux_mail_fields_not_in_fields_get_account_move(self):
        info = self.env["account.move"].with_user(self.user_u1).fields_get()
        leaked = [f for f in _MAIL_THREAD_AUX_FIELDS + _PORTAL_FIELDS if f in info]
        self.assertFalse(leaked, f"fields_get must not expose {leaked} on account.move")

    def test_rep_cannot_read_message_partner_ids_on_account_move(self):
        with self.assertRaises(AccessError):
            self.invoice.with_user(self.user_u1).read(["message_partner_ids"])

    def test_rep_cannot_read_message_attachment_count_on_account_move(self):
        with self.assertRaises(AccessError):
            self.invoice.with_user(self.user_u1).read(["message_attachment_count"])

    def test_rep_cannot_read_website_message_ids_on_account_move(self):
        with self.assertRaises(AccessError):
            self.invoice.with_user(self.user_u1).read(["website_message_ids"])

    # ------------------------------------------------------------------
    # res.partner
    # ------------------------------------------------------------------

    def test_rep_aux_mail_fields_not_in_fields_get_res_partner(self):
        info = self.env["res.partner"].with_user(self.user_u1).fields_get()
        leaked = [f for f in _MAIL_THREAD_AUX_FIELDS if f in info]
        self.assertFalse(leaked, f"fields_get must not expose {leaked} on res.partner")

    def test_rep_cannot_read_message_partner_ids_on_res_partner(self):
        with self.assertRaises(AccessError):
            self.customer_c1.with_user(self.user_u1).read(["message_partner_ids"])

    def test_rep_cannot_read_message_attachment_count_on_res_partner(self):
        with self.assertRaises(AccessError):
            self.customer_c1.with_user(self.user_u1).read(["message_attachment_count"])

    # ------------------------------------------------------------------
    # message_main_attachment_id (PR 13 — restored after PR 12 removed it)
    # ------------------------------------------------------------------

    def test_rep_cannot_read_message_main_attachment_id_on_sale_order(self):
        with self.assertRaises(AccessError):
            self.order.with_user(self.user_u1).read(["message_main_attachment_id"])

    def test_rep_cannot_read_message_main_attachment_id_on_account_move(self):
        with self.assertRaises(AccessError):
            self.invoice.with_user(self.user_u1).read(["message_main_attachment_id"])

    def test_rep_cannot_read_message_main_attachment_id_on_res_partner(self):
        with self.assertRaises(AccessError):
            self.customer_c1.with_user(self.user_u1).read(
                ["message_main_attachment_id"]
            )

    def test_rep_can_post_message_with_attachment_on_sale_order(self):
        """message_post with attachment must not raise AccessError for rep.

        mail.thread calls _message_set_main_attachment_id via sudo()
        (mail_thread.py), so the groups=!rep block on the field does not
        prevent the write that records the main attachment.
        """
        attach = self.env["ir.attachment"].create(
            {
                "name": "test.pdf",
                "datas": "dGVzdA==",  # base64 "test"
                "res_model": "sale.order",
                "res_id": self.order.id,
            }
        )
        self.order.with_user(self.user_u1).message_post(
            body="Rep note with attachment",
            attachment_ids=[attach.id],
            email_from="rep@example.com",
        )
        # The field was set (verified via sudo — rep cannot read it directly).
        self.assertTrue(self.order.sudo().message_main_attachment_id)

    # ------------------------------------------------------------------
    # Regression: admin is unaffected
    # ------------------------------------------------------------------

    def test_admin_can_read_message_partner_ids_on_sale_order(self):
        values = self.order.sudo().read(["message_partner_ids"])
        self.assertEqual(len(values), 1)
        self.assertIn("message_partner_ids", values[0])

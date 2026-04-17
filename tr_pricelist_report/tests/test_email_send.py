# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.addons.mail.tests.common import MailCommon

from .common import PricelistReportTestCommon


class TestEmailSend(PricelistReportTestCommon, MailCommon):
    """Cover the ``send_by_email`` branch of ``action_generate`` (§10)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer.email = "customer@example.com"

    def _open_email_wizard(self, **overrides):
        values = {
            "condition_id": self.condition.id,
            "layout": "por_categoria",
            "category_ids": [(6, 0, [self.categ_chemicals.id])],
            "send_by_email": True,
        }
        values.update(overrides)
        return self.env["tr.pricelist.report.wizard"].create(values)

    def test_download_path_unchanged_when_not_sending(self):
        """``send_by_email=False`` keeps the PR1-4 behavior."""
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        action = wizard.action_generate()
        self.assertEqual(action["type"], "ir.actions.report")

    def test_send_path_returns_act_window_for_compose(self):
        """``send_by_email=True`` routes to a mail.compose.message window."""
        wizard = self._open_email_wizard()
        action = wizard.action_generate()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "mail.compose.message")
        ctx = action["context"]
        self.assertEqual(ctx["default_model"], "partner.commercial.condition")
        self.assertEqual(ctx["default_res_id"], self.condition.id)
        self.assertEqual(ctx["default_composition_mode"], "comment")
        self.assertTrue(ctx.get("default_use_template"))
        template = self.env.ref("tr_pricelist_report.email_template_pricelist")
        self.assertEqual(ctx["default_template_id"], template.id)

    def test_attachment_is_created_as_temporary_for_composer(self):
        """PDF attachment nasce temporário no composer, não na condição.

        Codex 2026-04-17: se o vendedor abrir o composer e cancelar, o
        anexo não pode sobrar colado à condição. Padrão do core mail é
        criar attachment com ``res_model='mail.compose.message'`` e
        ``res_id=0``; na hora que o email sai, ``message_post`` re-
        parenta o anexo pro doc. Cancelar deixa o anexo órfão e o GC
        built-in do Odoo limpa.
        """
        wizard = self._open_email_wizard()
        action = wizard.action_generate()
        attachment_ids = action["context"]["default_attachment_ids"]
        self.assertEqual(len(attachment_ids), 1)
        attachment = self.env["ir.attachment"].browse(attachment_ids[0])
        self.assertEqual(attachment.res_model, "mail.compose.message")
        self.assertEqual(attachment.res_id, 0)
        self.assertEqual(attachment.mimetype, "application/pdf")
        # default_attachment_ids must be a list of raw ids, not an M2M command.
        for value in attachment_ids:
            self.assertIsInstance(value, int)

    def test_template_renders_subject_and_recipient_from_condition(self):
        """Template renders ``object.partner_id`` on subject / partner_to."""
        template = self.env.ref("tr_pricelist_report.email_template_pricelist")
        rendered_subject = template._render_field("subject", [self.condition.id])[
            self.condition.id
        ]
        self.assertIn(self.customer.name, rendered_subject)
        rendered_partner_to = template._render_field("partner_to", [self.condition.id])[
            self.condition.id
        ]
        self.assertEqual(str(self.customer.id), rendered_partner_to)

    def test_partner_without_email_still_opens_composer(self):
        """No email on the partner is not a blocker — composer still opens."""
        self.customer.email = False
        wizard = self._open_email_wizard()
        action = wizard.action_generate()
        self.assertEqual(action["res_model"], "mail.compose.message")

    def test_composer_send_triggers_mail_message_with_attachment(self):
        """End-to-end: submitting the composer posts on the condition.

        We create the composer with the same context the wizard passes
        on the UI, then force-apply the template
        (``_onchange_template_id_wrapper``) to reproduce the Odoo UI
        flow — the onchange is what fills partner_ids, subject and
        body when the template field is populated via default. Finally
        ``_action_send_mail()`` sends and we check:

        - a ``mail.message`` landed on the condition's chatter;
        - the PDF attachment is tied to it;
        - the attachment was re-parented from the temporary
          ``mail.compose.message`` bucket to the condition (Codex
          2026-04-17).
        """
        wizard = self._open_email_wizard()
        action = wizard.action_generate()
        ctx = action["context"]
        attachment = self.env["ir.attachment"].browse(ctx["default_attachment_ids"][0])
        self.assertEqual(attachment.res_model, "mail.compose.message")
        messages_before = self.condition.message_ids
        with self.mock_mail_gateway():
            composer = self.env["mail.compose.message"].with_context(**ctx).create({})
            composer._onchange_template_id_wrapper()
            composer._action_send_mail()
        new_messages = self.condition.message_ids - messages_before
        self.assertEqual(len(new_messages), 1)
        attachments = new_messages.attachment_ids
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments.mimetype, "application/pdf")
        self.assertIn(self.customer, new_messages.partner_ids)
        attachment.invalidate_recordset()
        self.assertEqual(attachment.res_model, "partner.commercial.condition")
        self.assertEqual(attachment.res_id, self.condition.id)

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import SalesRepAccessTestCommon


@tagged("post_install", "-at_install")
class TestRepPartnerBlock(SalesRepAccessTestCommon):
    """Coverage for the PR 4b guard on ``res.partner.write``.

    Rep users cannot modify an Active customer directly: every
    cadastral change must go through ``tr.partner.change.request``,
    and the commercial condition goes through the dedicated action
    buttons in ``tr_commercial_policy`` (which use ``.sudo()``). The
    guard leaves Draft partners editable for the pre-approval flow
    of PR 3, and allows chatter / scheduled activities so the rep
    can keep coordinating with the Sales Manager.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.active_stage = cls.env.ref("partner_stage.partner_stage_active")
        cls.draft_stage = cls.env.ref("partner_stage.partner_stage_draft")

        # Make sure C1 is Active (default is Active but defensive).
        cls.customer_c1.sudo().write({"stage_id": cls.active_stage.id})

        # The ``message_post`` chatter test needs the rep partner
        # and the user to have an email so Odoo can build a sender
        # address — otherwise ``_message_compute_author`` raises
        # before reaching the mail backend (test env stubs SMTP
        # but still validates sender resolution).
        cls.agent_a1.sudo().write({"email": "rep1@example.com"})
        cls.user_u1.sudo().write({"email": "rep1@example.com"})

        # Mail ICPs required by ``message_post`` and by
        # ``notify_on_create`` on tier definitions (end-to-end
        # approve tests trigger a mail when the tier review is
        # created). Without them, test env fails in
        # ``mail.mail._send``.
        cls.env["ir.config_parameter"].sudo().set_param(
            "mail.catchall.domain", "example.com"
        )
        cls.env["ir.config_parameter"].sudo().set_param(
            "mail.default.from", "noreply@example.com"
        )

    # ------------------------------------------------------------------
    # Cadastral writes on Active → blocked
    # ------------------------------------------------------------------

    def test_rep_cannot_write_phone_on_active_partner(self):
        with self.assertRaises(AccessError):
            self.customer_c1.with_user(self.user_u1).write({"phone": "+55 11 0"})

    def test_rep_cannot_write_name_on_active_partner(self):
        with self.assertRaises(AccessError):
            self.customer_c1.with_user(self.user_u1).write({"name": "Hijacked"})

    def test_rep_cannot_write_vat_on_active_partner(self):
        with self.assertRaises(AccessError):
            self.customer_c1.with_user(self.user_u1).write({"vat": "12345678"})

    def test_rep_cannot_write_street_on_active_partner(self):
        with self.assertRaises(AccessError):
            self.customer_c1.with_user(self.user_u1).write({"street": "New Street"})

    # ------------------------------------------------------------------
    # Draft stage keeps the rep in charge of cadastral edits
    # ------------------------------------------------------------------

    def test_rep_can_write_during_draft_stage(self):
        """Guard da 4b não aciona quando ``state != 'confirmed'``.

        Usa um partner Draft criado via admin (sem passar pelo
        override do rep que dispara ``request_validation``) para
        isolar o guard da 4b do ``partner_tier_validation`` da PR 3
        — o objetivo é provar apenas que o guard de write do rep
        ignora partners Draft, não relitigar a interação com tier.
        """
        draft = (
            self.env["res.partner"]
            .sudo()
            .create(
                {
                    "name": "Draft Customer",
                    "stage_id": self.draft_stage.id,
                    "agent_ids": [(6, 0, [self.agent_a1.id])],
                }
            )
        )
        self.assertEqual(draft.state, "draft")
        draft.with_user(self.user_u1).write({"phone": "+55 22 1234-5678"})
        self.assertEqual(draft.phone, "+55 22 1234-5678")

    # ------------------------------------------------------------------
    # Chatter and activities stay open on Active partners
    # ------------------------------------------------------------------

    def test_rep_can_post_message_on_active_partner(self):
        """``message_post`` writes message_ids (in the allowlist)."""
        self.customer_c1.with_user(self.user_u1).message_post(
            body="Ligo amanhã de manhã."
        )
        # message_post returned without raising; that is the assertion.

    def test_rep_can_schedule_activity_on_active_partner(self):
        """``activity_schedule`` writes activity_ids (in the allowlist)."""
        activity_type = self.env.ref("mail.mail_activity_data_todo")
        self.customer_c1.with_user(self.user_u1).activity_schedule(
            activity_type_id=activity_type.id, summary="Follow up"
        )
        # activity_schedule returned without raising; that is the assertion.

    def test_rep_cannot_bypass_guard_via_rpc_context_flag(self):
        """The create-scope flag is thread-local, not a context key.

        Regression defense: setting the (previous) context key
        via RPC must not bypass the guard. If someone ever
        reintroduces a context-based flag, this test breaks
        immediately.
        """
        with self.assertRaises(AccessError):
            self.customer_c1.with_user(self.user_u1).with_context(
                _tr_sales_rep_access_in_create=True
            ).write({"phone": "+55 99 0000-0000"})

    def test_rep_write_allowlist_only_bypasses_guard(self):
        """Write whose ``vals`` contains only allowlist fields → OK.

        ``message_post`` / ``activity_schedule`` typically wrap the
        write in ``sudo()``, hitting the ``env.su`` branch first.
        Here we exercise the ``sensitive == set()`` branch directly:
        an empty-vals ``write`` from a rep on an Active partner
        must go through, because there is nothing sensitive to
        reject.
        """
        # ``write({})`` is a valid no-op in Odoo that still goes
        # through the override — exactly the shape needed to
        # exercise the ``not sensitive`` guard branch.
        self.customer_c1.with_user(self.user_u1).write({})

    # ------------------------------------------------------------------
    # Internal sudo paths (change_request approve) bypass the guard
    # ------------------------------------------------------------------

    def test_change_request_approve_applies_via_sudo_bypass(self):
        """End-to-end: rep → change_request → manager approve → partner updated."""
        # Mail ICPs required by notify_on_create on the tier definition.
        self.env["ir.config_parameter"].sudo().set_param(
            "mail.catchall.domain", "example.com"
        )
        self.env["ir.config_parameter"].sudo().set_param(
            "mail.default.from", "noreply@example.com"
        )
        req = (
            self.env["tr.partner.change.request"]
            .with_user(self.user_u1)
            .create(
                {
                    "partner_id": self.customer_c1.id,
                    "request_type": "field_update",
                    "reason": "Customer moved.",
                    "new_phone": "+55 33 0000-0000",
                }
            )
        )
        admin = self.env.ref("base.user_admin")
        req.with_user(admin).action_approve()
        self.assertEqual(self.customer_c1.phone, "+55 33 0000-0000")

    def test_new_child_approve_creates_partner_via_sudo(self):
        """End-to-end: rep → new_child → approve → child created."""
        self.env["ir.config_parameter"].sudo().set_param(
            "mail.catchall.domain", "example.com"
        )
        self.env["ir.config_parameter"].sudo().set_param(
            "mail.default.from", "noreply@example.com"
        )
        req = (
            self.env["tr.partner.change.request"]
            .with_user(self.user_u1)
            .create(
                {
                    "partner_id": self.customer_c1.id,
                    "request_type": "new_child",
                    "reason": "Add buyer contact.",
                    "new_child_name": "Buyer",
                    "new_child_email": "buyer@example.com",
                }
            )
        )
        admin = self.env.ref("base.user_admin")
        req.with_user(admin).action_approve()
        self.assertTrue(req.processed_partner_id)
        self.assertEqual(req.processed_partner_id.parent_id, self.customer_c1)

    # ------------------------------------------------------------------
    # Non-rep users untouched
    # ------------------------------------------------------------------

    def test_admin_can_write_on_active_partner(self):
        self.customer_c1.write({"phone": "+55 44 9999-8888"})
        self.assertEqual(self.customer_c1.phone, "+55 44 9999-8888")

    def test_manager_without_rep_group_can_write(self):
        # base.group_partner_manager is required to edit contacts
        # (Contact Creation). Without it, the manager hits the
        # vanilla ACL before even reaching the PR 4b guard.
        manager = self.env["res.users"].create(
            {
                "name": "Pure Sales Manager",
                "login": "tsra_4b_manager",
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            self.env.ref("base.group_user").id,
                            self.env.ref("base.group_partner_manager").id,
                            self.env.ref("tr_commercial_policy.group_sales_manager").id,
                        ],
                    )
                ],
            }
        )
        self.customer_c1.with_user(manager).write({"phone": "+55 55 1111-2222"})
        self.assertEqual(self.customer_c1.phone, "+55 55 1111-2222")

    # ------------------------------------------------------------------
    # Active child contact also blocked (intentional limitation)
    # ------------------------------------------------------------------

    def test_rep_cannot_write_on_active_child_contact(self):
        child = self.env["res.partner"].create(
            {"name": "Buyer", "parent_id": self.customer_c1.id}
        )
        child.sudo().write({"stage_id": self.active_stage.id})
        with self.assertRaises(AccessError):
            child.with_user(self.user_u1).write({"phone": "+55 66 0"})

    # ------------------------------------------------------------------
    # Commercial-policy action buttons keep working on Active partners
    # (opção B: actions use ``.sudo()`` internally to set
    # ``commercial_condition_id``)
    # ------------------------------------------------------------------

    def _ensure_company_group(self):
        """Give customer_c1 a company_group with a condition so
        override/remove-override have a template to copy from and
        a group to fall back to.
        """
        group = (
            self.env["res.partner"]
            .sudo()
            .create({"name": "Test Company Group", "is_company": True})
        )
        condition = (
            self.env["partner.commercial.condition"]
            .sudo()
            .create({"partner_id": group.id, "pricelist_id": self.pricelist.id})
        )
        group.sudo().write({"commercial_condition_id": condition.id})
        self.customer_c1.sudo().write({"company_group_id": group.id})
        return group

    def test_rep_can_trigger_action_create_commercial_condition_on_active_partner(
        self,
    ):
        self.customer_c1.sudo().write({"commercial_condition_id": False})
        self.customer_c1.with_user(self.user_u1).action_create_commercial_condition()
        self.assertTrue(self.customer_c1.commercial_condition_id)

    def test_rep_can_trigger_action_create_override_condition_on_active_partner(self):
        self._ensure_company_group()
        self.customer_c1.sudo().write({"commercial_condition_id": False})
        self.customer_c1.with_user(self.user_u1).action_create_override_condition()
        self.assertTrue(self.customer_c1.commercial_condition_id)
        self.assertEqual(
            self.customer_c1.commercial_condition_id.partner_id, self.customer_c1
        )

    def test_rep_can_trigger_action_remove_override_condition_on_active_partner(self):
        self._ensure_company_group()
        self.customer_c1.with_user(self.user_u1).action_create_override_condition()
        self.assertTrue(self.customer_c1.commercial_condition_id)
        self.customer_c1.with_user(self.user_u1).action_remove_override_condition()
        self.assertFalse(self.customer_c1.commercial_condition_id)

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import SalesRepAccessTestCommon


@tagged("post_install", "-at_install")
class TestChangeRequest(SalesRepAccessTestCommon):
    """Coverage for tr.partner.change.request workflow (PR 4a).

    Workflow:
      - rep opens a change request against an Active customer
      - tier fires automatically (notify_on_create=True + request_validation)
      - Sales Manager approves/rejects, or the rep cancels
      - only one pending request per customer at a time
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.active_stage = cls.env.ref("partner_stage.partner_stage_active")

        # Move C1 and C2 to Active explicitly (they start confirmed via
        # partner_stage default, but make it defensive in case other
        # modules shift the default in the future).
        (cls.customer_c1 | cls.customer_c2).sudo().write(
            {"stage_id": cls.active_stage.id}
        )

        # Mail ICPs required by notify_on_create on the tier
        # definition: when a review is created, the tier sends a
        # notification email via mail.thread. Without these, the
        # test env fails at mail.mail._send (AssertionError on
        # "provide a sender address explicitly").
        cls.env["ir.config_parameter"].sudo().set_param(
            "mail.catchall.domain", "example.com"
        )
        cls.env["ir.config_parameter"].sudo().set_param(
            "mail.default.from", "noreply@example.com"
        )

        cls.manager_group = cls.env.ref("tr_commercial_policy.group_sales_manager")
        cls.director_group = cls.env.ref("tr_commercial_policy.group_sales_director")

        cls.phone_field = cls.env["ir.model.fields"].search(
            [("model", "=", "res.partner"), ("name", "=", "phone")], limit=1
        )
        cls.email_field = cls.env["ir.model.fields"].search(
            [("model", "=", "res.partner"), ("name", "=", "email")], limit=1
        )
        cls.country_field = cls.env["ir.model.fields"].search(
            [("model", "=", "res.partner"), ("name", "=", "country_id")], limit=1
        )
        cls.br_country = cls.env.ref("base.br")
        cls.sp_state = cls.env.ref("base.state_br_sp")

    def _create_field_update_request(
        self, user, customer, lines, reason="Customer moved."
    ):
        """Helper that creates a field_update request as a given user."""
        return (
            self.env["tr.partner.change.request"]
            .with_user(user)
            .create(
                {
                    "partner_id": customer.id,
                    "request_type": "field_update",
                    "reason": reason,
                    "line_ids": lines,
                }
            )
        )

    # ------------------------------------------------------------------
    # Happy path create
    # ------------------------------------------------------------------

    def test_rep_creates_field_update_request(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (
                    0,
                    0,
                    {
                        "field_id": self.phone_field.id,
                        "new_value_char": "+55 11 9999-0000",
                    },
                ),
            ],
        )
        self.assertEqual(req.state, "pending")
        self.assertEqual(req.sales_rep_partner_id, self.agent_a1)
        self.assertTrue(
            req.review_ids,
            "Tier reviews must be created automatically on request creation.",
        )

    def test_sales_rep_partner_id_snapshot_on_create(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
            ],
        )
        self.assertEqual(req.sales_rep_partner_id, self.user_u1.partner_id)

    # ------------------------------------------------------------------
    # Unicidade
    # ------------------------------------------------------------------

    def test_rep_cannot_open_second_pending_request_on_same_partner(self):
        self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "a"}),
            ],
        )
        with self.assertRaises(ValidationError):
            self._create_field_update_request(
                self.user_u1,
                self.customer_c1,
                [
                    (
                        0,
                        0,
                        {"field_id": self.email_field.id, "new_value_char": "x@y.com"},
                    ),
                ],
            )

    def test_rep_can_cancel_and_create_new_request(self):
        first = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "a"}),
            ],
        )
        first.with_user(self.user_u1).action_cancel()
        self.assertEqual(first.state, "cancelled")
        second = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.email_field.id, "new_value_char": "x@y.com"}),
            ],
        )
        self.assertEqual(second.state, "pending")

    def test_cancel_by_another_rep_raises(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "a"}),
            ],
        )
        with self.assertRaises(AccessError):
            req.with_user(self.user_u2).action_cancel()

    # ------------------------------------------------------------------
    # Whitelist / payload
    # ------------------------------------------------------------------

    def test_line_with_foreign_field_raises(self):
        # vat is not in the whitelist.
        vat_field = self.env["ir.model.fields"].search(
            [("model", "=", "res.partner"), ("name", "=", "vat")], limit=1
        )
        self.assertTrue(vat_field, "vat field must exist for this test")
        with self.assertRaises(ValidationError):
            self._create_field_update_request(
                self.user_u1,
                self.customer_c1,
                [
                    (0, 0, {"field_id": vat_field.id, "new_value_char": "123"}),
                ],
            )

    def test_line_with_mismatched_value_raises(self):
        # phone is char -> a reference value should not be accepted.
        with self.assertRaises(ValidationError):
            self._create_field_update_request(
                self.user_u1,
                self.customer_c1,
                [
                    (
                        0,
                        0,
                        {
                            "field_id": self.phone_field.id,
                            "new_value_reference": "res.country,%s"
                            % self.br_country.id,
                        },
                    ),
                ],
            )

    def test_line_with_mismatched_m2o_model_raises(self):
        # country_id expects res.country; a state reference must fail.
        with self.assertRaises(ValidationError):
            self._create_field_update_request(
                self.user_u1,
                self.customer_c1,
                [
                    (
                        0,
                        0,
                        {
                            "field_id": self.country_field.id,
                            "new_value_reference": "res.country.state,%s"
                            % self.sp_state.id,
                        },
                    ),
                ],
            )

    # ------------------------------------------------------------------
    # Request type payload
    # ------------------------------------------------------------------

    def test_new_child_request_requires_name(self):
        with self.assertRaises(ValidationError):
            self.env["tr.partner.change.request"].with_user(self.user_u1).create(
                {
                    "partner_id": self.customer_c1.id,
                    "request_type": "new_child",
                    "reason": "Buyer changed.",
                }
            )

    def test_new_child_request_cannot_have_lines(self):
        with self.assertRaises(ValidationError):
            self.env["tr.partner.change.request"].with_user(self.user_u1).create(
                {
                    "partner_id": self.customer_c1.id,
                    "request_type": "new_child",
                    "reason": "Buyer changed.",
                    "new_child_name": "New Buyer",
                    "line_ids": [
                        (
                            0,
                            0,
                            {
                                "field_id": self.phone_field.id,
                                "new_value_char": "x",
                            },
                        )
                    ],
                }
            )

    def test_field_update_with_child_data_raises(self):
        """Field update with new_child_name at create fails the constraint."""
        with self.assertRaises(ValidationError):
            self.env["tr.partner.change.request"].with_user(self.user_u1).create(
                {
                    "partner_id": self.customer_c1.id,
                    "request_type": "field_update",
                    "reason": "Mixed payload",
                    "new_child_name": "Hijack",
                    "line_ids": [
                        (
                            0,
                            0,
                            {
                                "field_id": self.phone_field.id,
                                "new_value_char": "x",
                            },
                        ),
                    ],
                }
            )

    def test_field_update_without_lines_raises(self):
        """Field update type requires at least one line."""
        with self.assertRaises(ValidationError):
            self.env["tr.partner.change.request"].with_user(self.user_u1).create(
                {
                    "partner_id": self.customer_c1.id,
                    "request_type": "field_update",
                    "reason": "No lines",
                }
            )

    def test_compute_name_on_new_record(self):
        """Name compute covers the NewId branch (before save)."""
        draft = self.env["tr.partner.change.request"].new(
            {"partner_id": self.customer_c1.id}
        )
        self.assertIn("New change request", draft.name)

    # ------------------------------------------------------------------
    # Approve / reject
    # ------------------------------------------------------------------

    def _admin(self):
        return self.env.ref("base.user_admin")

    def test_manager_approve_applies_field_update(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (
                    0,
                    0,
                    {
                        "field_id": self.phone_field.id,
                        "new_value_char": "+55 11 9999-0001",
                    },
                ),
            ],
        )
        req.with_user(self._admin()).action_approve()
        self.assertEqual(req.state, "approved")
        self.assertEqual(self.customer_c1.phone, "+55 11 9999-0001")

    def test_manager_approve_creates_new_child(self):
        req = (
            self.env["tr.partner.change.request"]
            .with_user(self.user_u1)
            .create(
                {
                    "partner_id": self.customer_c1.id,
                    "request_type": "new_child",
                    "reason": "Add financial contact.",
                    "new_child_name": "Financial Contact",
                    "new_child_email": "fin@example.com",
                    "new_child_function": "CFO",
                }
            )
        )
        req.with_user(self._admin()).action_approve()
        self.assertEqual(req.state, "approved")
        self.assertTrue(req.processed_partner_id)
        child = req.processed_partner_id
        self.assertEqual(child.parent_id, self.customer_c1)
        self.assertEqual(child.name, "Financial Contact")
        self.assertEqual(child.email, "fin@example.com")

    def test_manager_reject(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "+55"}),
            ],
        )
        req.with_user(self._admin()).action_reject()
        self.assertEqual(req.state, "rejected")
        # Partner phone should remain untouched.
        self.assertFalse(self.customer_c1.phone)

    def test_action_reject_marks_reviews_and_transitions_state(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "+55"}),
            ],
        )
        req.with_user(self._admin()).action_reject()
        self.assertEqual(req.state, "rejected")
        self.assertTrue(req.review_ids)
        self.assertTrue(
            all(r.status == "rejected" for r in req.review_ids),
            "All tier reviews must be marked rejected after action_reject.",
        )

    def test_approve_without_manager_group_raises(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "+55"}),
            ],
        )
        with self.assertRaises(AccessError):
            req.with_user(self.user_u1).action_approve()

    def test_action_approve_raises_when_tier_not_validated(self):
        """Exercise validation_status branch with disjoint reviewer groups.

        A second tier.definition requires group_sales_director. A
        "Sales Manager Puro" user (only group_sales_manager) can
        only approve the first review, leaving the director one
        pending. action_approve must then raise UserError and leave
        the partner unchanged. Using admin here would be a false
        positive because admin implies both groups.
        """
        extra_def = self.env["tier.definition"].create(
            {
                "name": "Director extra approval",
                "model_id": self.env["ir.model"]._get_id("tr.partner.change.request"),
                "review_type": "group",
                "reviewer_group_id": self.director_group.id,
                "definition_type": "domain",
                "definition_domain": "[('state', '=', 'pending')]",
                "sequence": 20,
            }
        )
        try:
            pure_manager = self.env["res.users"].create(
                {
                    "name": "Pure Sales Manager",
                    "login": "tsra_pure_manager",
                    "groups_id": [(6, 0, [self.manager_group.id])],
                }
            )
            original_phone = self.customer_c1.phone
            req = self._create_field_update_request(
                self.user_u1,
                self.customer_c1,
                [
                    (0, 0, {"field_id": self.phone_field.id, "new_value_char": "+55"}),
                ],
            )
            # Two definitions matched the domain -> two reviews.
            self.assertEqual(len(req.review_ids), 2)
            with self.assertRaises(UserError):
                req.with_user(pure_manager).action_approve()
            self.assertEqual(req.state, "pending")
            self.assertEqual(self.customer_c1.phone, original_phone)
        finally:
            extra_def.active = False

    def test_action_approve_applies_only_after_validation(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "+55 22"}),
            ],
        )
        req.with_user(self._admin()).action_approve()
        self.assertEqual(req.state, "approved")
        self.assertEqual(self.customer_c1.phone, "+55 22")

    def test_tier_prevents_premature_approve(self):
        """Rep without manager group cannot approve — guard enforced."""
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
            ],
        )
        with self.assertRaises(AccessError):
            req.with_user(self.user_u1).action_approve()

    # ------------------------------------------------------------------
    # has_comment guard
    # ------------------------------------------------------------------

    def test_approve_raises_when_has_comment_enabled(self):
        tier_def = self.env.ref("tr_sales_rep_access.tier_def_partner_change_request")
        tier_def.has_comment = True
        try:
            req = self._create_field_update_request(
                self.user_u1,
                self.customer_c1,
                [
                    (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
                ],
            )
            with self.assertRaises(UserError) as cm:
                req.with_user(self._admin()).action_approve()
            self.assertIn("has_comment", str(cm.exception))
        finally:
            tier_def.has_comment = False

    # ------------------------------------------------------------------
    # Record rules
    # ------------------------------------------------------------------

    def test_rep_cannot_see_other_rep_request(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
            ],
        )
        # ``exists()`` does not apply record rules — use ``search``
        # to actually exercise the scope filter.
        visible = (
            self.env["tr.partner.change.request"]
            .with_user(self.user_u2)
            .search([("id", "=", req.id)])
        )
        self.assertFalse(visible, "Record rule must hide another rep's change request.")

    # ------------------------------------------------------------------
    # Immutable after pending
    # ------------------------------------------------------------------

    def test_rep_cannot_edit_line_after_approved(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
            ],
        )
        req.with_user(self._admin()).action_approve()
        line = req.line_ids
        with self.assertRaises(AccessError):
            line.with_user(self.user_u1).write({"new_value_char": "hijack"})

    def test_rep_cannot_unlink_line_after_rejected(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
            ],
        )
        req.with_user(self._admin()).action_reject()
        line = req.line_ids
        with self.assertRaises(AccessError):
            line.with_user(self.user_u1).unlink()

    def test_rep_cannot_create_line_on_approved_request(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
            ],
        )
        req.with_user(self._admin()).action_approve()
        with self.assertRaises(AccessError):
            self.env["tr.partner.change.request.line"].with_user(self.user_u1).create(
                {
                    "request_id": req.id,
                    "field_id": self.email_field.id,
                    "new_value_char": "hijack@example.com",
                }
            )

    def test_rep_cannot_edit_payload_after_pending_transition(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
            ],
        )
        req.with_user(self._admin()).action_approve()
        with self.assertRaises(AccessError):
            req.with_user(self.user_u1).write({"reason": "updated reason"})

    def test_manager_can_unlink_cancelled_request(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
            ],
        )
        req.with_user(self.user_u1).action_cancel()
        req.with_user(self._admin()).unlink()
        self.assertFalse(req.exists())

    # ------------------------------------------------------------------
    # Audit field immutability after create (no RPC forge)
    # ------------------------------------------------------------------

    def test_cannot_rpc_write_requested_by(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
            ],
        )
        with self.assertRaises(AccessError):
            req.sudo().write({"requested_by": self._admin().id})

    def test_cannot_rpc_write_sales_rep_partner_id(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
            ],
        )
        with self.assertRaises(AccessError):
            req.sudo().write({"sales_rep_partner_id": self.agent_a2.id})

    def test_cannot_rpc_write_processed_partner_id(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
            ],
        )
        req.with_user(self._admin()).action_approve()
        with self.assertRaises(AccessError):
            req.sudo().write({"processed_partner_id": self.customer_c2.id})

    # ------------------------------------------------------------------
    # Direct state write bypass (workflow-only transition)
    # ------------------------------------------------------------------

    def test_rep_cannot_rpc_write_state_to_approved(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
            ],
        )
        with self.assertRaises(AccessError):
            req.with_user(self.user_u1).write({"state": "approved"})

    def test_rep_cannot_rpc_write_state_to_rejected(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
            ],
        )
        with self.assertRaises(AccessError):
            req.with_user(self.user_u1).with_context(skip_validation_check=True).write(
                {"state": "rejected"}
            )

    def test_rep_cannot_rpc_write_state_to_cancelled(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
            ],
        )
        with self.assertRaises(AccessError):
            req.with_user(self.user_u1).write({"state": "cancelled"})

    # ------------------------------------------------------------------
    # Snapshot / authorship hardening
    # ------------------------------------------------------------------

    def test_rep_cannot_forge_requested_by(self):
        """Rep passing ``requested_by=admin`` via RPC is ignored.

        The create override forces ``requested_by=env.uid`` for rep
        users so a request always carries the real author — an
        audit invariant.
        """
        req = (
            self.env["tr.partner.change.request"]
            .with_user(self.user_u1)
            .create(
                {
                    "partner_id": self.customer_c1.id,
                    "request_type": "field_update",
                    "reason": "Forged?",
                    "requested_by": self._admin().id,
                    "line_ids": [
                        (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"})
                    ],
                }
            )
        )
        self.assertEqual(req.requested_by, self.user_u1)

    def test_rep_cannot_forge_sales_rep_partner_id(self):
        """Rep passing a foreign ``sales_rep_partner_id`` is ignored.

        The create override pins the snapshot to the acting rep's
        own partner. Without this, a rep could hide requests from
        themselves or surface one in another rep's visible scope.
        """
        req = (
            self.env["tr.partner.change.request"]
            .with_user(self.user_u1)
            .create(
                {
                    "partner_id": self.customer_c1.id,
                    "request_type": "field_update",
                    "reason": "Forged snapshot?",
                    "sales_rep_partner_id": self.agent_a2.id,
                    "line_ids": [
                        (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"})
                    ],
                }
            )
        )
        self.assertEqual(req.sales_rep_partner_id, self.agent_a1)

    # ------------------------------------------------------------------
    # Uniqueness across reps (record rule bypass defense)
    # ------------------------------------------------------------------

    def test_uniqueness_sees_other_reps_pending(self):
        """Unicidade roda sob sudo e pega pending invisível pro rep.

        Admin cria um pending em C1 com ``sales_rep_partner_id=
        agent_a2`` (simula um request legado de outro rep). U1 não
        enxerga esse pending pelo record rule. Sem ``sudo()`` no
        ``search_count``, a constraint acharia apenas os pending
        visíveis pra U1 e aceitaria o segundo — bypass. Com
        ``sudo()``, a colisão aparece.
        """
        foreign_pending = (
            self.env["tr.partner.change.request"]
            .sudo()
            .create(
                {
                    "partner_id": self.customer_c1.id,
                    "request_type": "field_update",
                    "reason": "Pre-existing request from another rep.",
                    "requested_by": self.user_u2.id,
                    "sales_rep_partner_id": self.agent_a2.id,
                    "line_ids": [
                        (
                            0,
                            0,
                            {
                                "field_id": self.phone_field.id,
                                "new_value_char": "something",
                            },
                        )
                    ],
                }
            )
        )
        try:
            visible = (
                self.env["tr.partner.change.request"]
                .with_user(self.user_u1)
                .search([("id", "=", foreign_pending.id)])
            )
            self.assertFalse(
                visible, "Foreign pending must be hidden from U1 by the rule."
            )
            with self.assertRaises(ValidationError):
                self._create_field_update_request(
                    self.user_u1,
                    self.customer_c1,
                    [
                        (
                            0,
                            0,
                            {
                                "field_id": self.email_field.id,
                                "new_value_char": "x@y.com",
                            },
                        ),
                    ],
                )
        finally:
            foreign_pending.sudo().with_context(
                _tr_change_request_internal_transition=True
            ).write({"state": "cancelled"})
            foreign_pending.sudo().unlink()

    # ------------------------------------------------------------------
    # Dangling reviews on terminal transitions
    # ------------------------------------------------------------------

    def _make_extra_tier(self):
        """Add a second tier.definition to force 2 reviews per request."""
        return self.env["tier.definition"].create(
            {
                "name": "Director extra approval",
                "model_id": self.env["ir.model"]._get_id("tr.partner.change.request"),
                "review_type": "group",
                "reviewer_group_id": self.director_group.id,
                "definition_type": "domain",
                "definition_domain": "[('state', '=', 'pending')]",
                "sequence": 20,
            }
        )

    def test_cancel_closes_all_pending_reviews(self):
        """Cancel must not leave tier.review records hanging in pending."""
        extra = self._make_extra_tier()
        try:
            req = self._create_field_update_request(
                self.user_u1,
                self.customer_c1,
                [
                    (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
                ],
            )
            self.assertEqual(len(req.review_ids), 2)
            req.with_user(self.user_u1).action_cancel()
            self.assertEqual(req.state, "cancelled")
            self.assertFalse(
                req.review_ids.filtered(lambda r: r.status == "pending"),
                "No pending review should remain after cancel.",
            )
        finally:
            extra.active = False

    def test_reject_closes_reviews_from_other_definitions(self):
        """Reject sweeps reviews from other tier definitions too."""
        extra = self._make_extra_tier()
        try:
            req = self._create_field_update_request(
                self.user_u1,
                self.customer_c1,
                [
                    (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
                ],
            )
            self.assertEqual(len(req.review_ids), 2)
            # Admin belongs to both groups, but reject_tier only
            # rejects the reviews it can; _close_pending_reviews
            # finishes the rest. After action_reject, no review
            # must stay pending.
            req.with_user(self._admin()).action_reject()
            self.assertEqual(req.state, "rejected")
            self.assertFalse(
                req.review_ids.filtered(lambda r: r.status == "pending"),
                "No pending review should remain after reject.",
            )
            self.assertTrue(
                all(r.status == "rejected" for r in req.review_ids),
                "All reviews must be marked rejected after action_reject.",
            )
        finally:
            extra.active = False

    def test_cancel_does_not_set_done_by_on_force_closed_reviews(self):
        """_close_pending_reviews must not record the canceller as done_by.

        When a rep cancels a request that has reviews from a tier they are
        not a reviewer of (e.g. a Manager tier), setting done_by=rep would
        make the audit trail show the rep as having rejected a Manager review.
        done_by must be False for force-closed reviews.

        Note: after action_cancel() the mixin deletes all reviews
        (_allow_to_remove_reviews returns True for _cancel_state), so we
        call _close_pending_reviews directly to inspect done_by before the
        deletion happens.
        """
        extra = self._make_extra_tier()
        try:
            req = self._create_field_update_request(
                self.user_u1,
                self.customer_c1,
                [
                    (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
                ],
            )
            self.assertEqual(len(req.review_ids), 2)
            req._close_pending_reviews()
            for review in req.review_ids:
                self.assertFalse(review.done_by)
        finally:
            extra.active = False

    # ------------------------------------------------------------------
    # Happy path write/unlink on pending lines (super() branches)
    # ------------------------------------------------------------------

    def test_rep_can_edit_line_while_pending(self):
        """Write on a line while request is still pending succeeds."""
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "old"}),
            ],
        )
        req.line_ids.with_user(self.user_u1).write({"new_value_char": "new"})
        self.assertEqual(req.line_ids.new_value_char, "new")

    def test_rep_can_unlink_line_while_pending(self):
        """Unlink a line while request is still pending succeeds."""
        req = (
            self.env["tr.partner.change.request"]
            .with_user(self.user_u1)
            .create(
                {
                    "partner_id": self.customer_c1.id,
                    "request_type": "field_update",
                    "reason": "two lines",
                    "line_ids": [
                        (
                            0,
                            0,
                            {"field_id": self.phone_field.id, "new_value_char": "a"},
                        ),
                        (
                            0,
                            0,
                            {"field_id": self.email_field.id, "new_value_char": "b@c"},
                        ),
                    ],
                }
            )
        )
        self.assertEqual(len(req.line_ids), 2)
        req.line_ids[0].with_user(self.user_u1).unlink()
        self.assertEqual(len(req.line_ids), 1)

    # ------------------------------------------------------------------
    # Terminal-state action guards (already-terminal raises)
    # ------------------------------------------------------------------

    def test_action_cancel_on_non_pending_raises(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
            ],
        )
        req.with_user(self._admin()).action_approve()
        with self.assertRaises(UserError):
            req.with_user(self.user_u1).action_cancel()

    def test_action_reject_on_non_pending_raises(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
            ],
        )
        req.with_user(self._admin()).action_approve()
        with self.assertRaises(UserError):
            req.with_user(self._admin()).action_reject()

    def test_action_approve_on_non_pending_raises(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
            ],
        )
        req.with_user(self._admin()).action_approve()
        with self.assertRaises(UserError):
            req.with_user(self._admin()).action_approve()

    # ------------------------------------------------------------------
    # Unlink rejection paths
    # ------------------------------------------------------------------

    def test_rep_cannot_unlink_request_at_all(self):
        """Rep has no D on request; unlink raises AccessError."""
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
            ],
        )
        req.with_user(self._admin()).action_cancel()
        with self.assertRaises(AccessError):
            req.with_user(self.user_u1).unlink()

    # ------------------------------------------------------------------
    # Admin-on-behalf-of-rep create path
    # ------------------------------------------------------------------

    def test_admin_creates_request_on_behalf_of_rep(self):
        """Admin (non-rep) can author a request; snapshot derived from requested_by."""
        req = self.env["tr.partner.change.request"].create(
            {
                "partner_id": self.customer_c1.id,
                "request_type": "field_update",
                "reason": "Rep was offline; admin filed on his behalf.",
                "requested_by": self.user_u1.id,
                "line_ids": [
                    (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
                ],
            }
        )
        self.assertEqual(req.requested_by, self.user_u1)
        self.assertEqual(req.sales_rep_partner_id, self.agent_a1)

    # ------------------------------------------------------------------
    # Payload type mismatches (m2o branches)
    # ------------------------------------------------------------------

    def test_line_m2o_without_reference_raises(self):
        with self.assertRaises(ValidationError):
            self._create_field_update_request(
                self.user_u1,
                self.customer_c1,
                [
                    (0, 0, {"field_id": self.country_field.id}),
                ],
            )

    def test_line_m2o_with_char_raises(self):
        with self.assertRaises(ValidationError):
            self._create_field_update_request(
                self.user_u1,
                self.customer_c1,
                [
                    (
                        0,
                        0,
                        {
                            "field_id": self.country_field.id,
                            "new_value_char": "Brazil",
                            "new_value_reference": "res.country,%s"
                            % self.br_country.id,
                        },
                    ),
                ],
            )

    def test_apply_field_update_m2o_resolves_reference(self):
        """Approve with m2o payload: partner.country_id = reference target."""
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (
                    0,
                    0,
                    {
                        "field_id": self.country_field.id,
                        "new_value_reference": "res.country,%s" % self.br_country.id,
                    },
                ),
            ],
        )
        req.with_user(self._admin()).action_approve()
        self.assertEqual(self.customer_c1.country_id, self.br_country)

    def test_cannot_unlink_approved_request(self):
        req = self._create_field_update_request(
            self.user_u1,
            self.customer_c1,
            [
                (0, 0, {"field_id": self.phone_field.id, "new_value_char": "x"}),
            ],
        )
        req.with_user(self._admin()).action_approve()
        with self.assertRaises(AccessError):
            req.with_user(self._admin()).unlink()

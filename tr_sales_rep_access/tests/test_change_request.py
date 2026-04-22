# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import SalesRepAccessTestCommon


@tagged("post_install", "-at_install")
class TestChangeRequest(SalesRepAccessTestCommon):
    """Coverage for tr.partner.change.request with the fixed-field
    payload (no more line_ids / ir.model.fields picker).

    Workflow:
      - rep opens a request against an Active customer with the
        current values pre-filled in new_*
      - only fields that differ from the current partner value get
        applied; empty values clear the partner field
      - manager approves/rejects, rep can cancel
      - only one pending request per customer at a time
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.active_stage = cls.env.ref("partner_stage.partner_stage_active")
        for customer in (cls.customer_c1, cls.customer_c2):
            customer.sudo().write(
                {
                    "stage_id": cls.active_stage.id,
                    "phone": "+55 11 1111-1111",
                    "email": "old@example.com",
                    "street": "Old Street",
                    "city": "Old City",
                }
            )
        cls.manager_user = cls.env["res.users"].create(
            {
                "name": "Manager",
                "login": "test_change_request_manager",
                "groups_id": [
                    (4, cls.env.ref("base.group_user").id),
                    (
                        4,
                        cls.env.ref("tr_commercial_policy.group_sales_manager").id,
                    ),
                ],
            }
        )

    def _create_field_update(self, customer, **new_values):
        """Rep creates a field_update request with prefill from the
        partner and the given overrides layered on top."""
        CR = self.env["tr.partner.change.request"].with_user(self.user_u1)
        request = CR.new(
            {
                "partner_id": customer.id,
                "request_type": "field_update",
                "reason": "test",
            }
        )
        request._onchange_partner_id_prefill()
        vals = request._convert_to_write(request._cache)
        vals.update(new_values)
        return CR.create(vals)

    # ------------------------------------------------------------------
    # Prefill from partner
    # ------------------------------------------------------------------

    def test_prefill_copies_current_partner_values(self):
        request = (
            self.env["tr.partner.change.request"]
            .with_user(self.user_u1)
            .new(
                {
                    "partner_id": self.customer_c1.id,
                    "request_type": "field_update",
                    "reason": "t",
                }
            )
        )
        request._onchange_partner_id_prefill()
        self.assertEqual(request.new_phone, self.customer_c1.phone)
        self.assertEqual(request.new_email, self.customer_c1.email)
        self.assertEqual(request.new_street, self.customer_c1.street)
        self.assertEqual(request.new_name, self.customer_c1.name)

    # ------------------------------------------------------------------
    # Apply: only changed fields are written
    # ------------------------------------------------------------------

    def test_apply_writes_only_changed_field(self):
        request = self._create_field_update(
            self.customer_c1, new_phone="+55 11 9999-9999"
        )
        original_email = self.customer_c1.email
        request.with_user(self.manager_user).action_approve()
        self.customer_c1.invalidate_recordset()
        self.assertEqual(self.customer_c1.phone, "+55 11 9999-9999")
        self.assertEqual(self.customer_c1.email, original_email)

    def test_apply_can_clear_value(self):
        request = self._create_field_update(self.customer_c1, new_phone=False)
        request.with_user(self.manager_user).action_approve()
        self.customer_c1.invalidate_recordset()
        self.assertFalse(self.customer_c1.phone)

    def test_apply_many2one_field(self):
        br = self.env.ref("base.br")
        request = self._create_field_update(self.customer_c1, new_country_id=br.id)
        request.with_user(self.manager_user).action_approve()
        self.customer_c1.invalidate_recordset()
        self.assertEqual(self.customer_c1.country_id, br)

    # ------------------------------------------------------------------
    # Payload type constraint
    # ------------------------------------------------------------------

    def test_field_update_rejects_new_child_payload(self):
        with self.assertRaises(ValidationError):
            self.env["tr.partner.change.request"].with_user(self.user_u1).create(
                {
                    "partner_id": self.customer_c1.id,
                    "request_type": "field_update",
                    "reason": "x",
                    "new_child_name": "Kid",
                }
            )

    def test_new_child_requires_name(self):
        with self.assertRaises(ValidationError):
            self.env["tr.partner.change.request"].with_user(self.user_u1).create(
                {
                    "partner_id": self.customer_c1.id,
                    "request_type": "new_child",
                    "reason": "x",
                }
            )

    # ------------------------------------------------------------------
    # Immutability: cannot edit payload fields after pending
    # ------------------------------------------------------------------

    def test_cannot_edit_payload_after_cancelled(self):
        request = self._create_field_update(
            self.customer_c1, new_phone="+55 11 2222-2222"
        )
        request.with_user(self.user_u1).action_cancel()
        with self.assertRaises(AccessError):
            request.with_user(self.user_u1).write({"new_phone": "+55 11 0000-0000"})

    # ------------------------------------------------------------------
    # Workflow actions
    # ------------------------------------------------------------------

    def test_rep_can_cancel_own_pending_request(self):
        request = self._create_field_update(
            self.customer_c1, new_phone="+55 11 3333-3333"
        )
        request.with_user(self.user_u1).action_cancel()
        self.assertEqual(request.state, "cancelled")

    def test_manager_rejects_request(self):
        request = self._create_field_update(
            self.customer_c1, new_phone="+55 11 4444-4444"
        )
        request.with_user(self.manager_user).action_reject()
        self.assertEqual(request.state, "rejected")
        self.customer_c1.invalidate_recordset()
        self.assertEqual(self.customer_c1.phone, "+55 11 1111-1111")

    def test_rep_cannot_reject(self):
        request = self._create_field_update(
            self.customer_c1, new_phone="+55 11 5555-5555"
        )
        with self.assertRaises(AccessError):
            request.with_user(self.user_u1).action_reject()

    @mute_logger("odoo.sql_db")
    def test_only_one_pending_per_partner(self):
        self._create_field_update(self.customer_c1, new_phone="+55 11 6666-6666")
        # The SQL partial unique index fires before the Python
        # @api.constrains — covers concurrent inserts that the
        # constraint cannot see inside the same transaction.
        with self.assertRaises(IntegrityError):
            self._create_field_update(self.customer_c1, new_phone="+55 11 7777-7777")

    # ------------------------------------------------------------------
    # new_child still works end-to-end (regression)
    # ------------------------------------------------------------------

    def test_new_child_approve_creates_contact(self):
        request = (
            self.env["tr.partner.change.request"]
            .with_user(self.user_u1)
            .create(
                {
                    "partner_id": self.customer_c1.id,
                    "request_type": "new_child",
                    "reason": "child",
                    "new_child_name": "Kid Contact",
                    "new_child_email": "kid@example.com",
                    "new_child_type": "contact",
                }
            )
        )
        request.with_user(self.manager_user).action_approve()
        self.assertEqual(request.state, "approved")
        self.assertTrue(request.processed_partner_id)
        self.assertEqual(request.processed_partner_id.name, "Kid Contact")
        self.assertEqual(request.processed_partner_id.parent_id, self.customer_c1)

    # ------------------------------------------------------------------
    # Audit fields immutability
    # ------------------------------------------------------------------

    def test_requested_by_immutable(self):
        request = self._create_field_update(self.customer_c1)
        with self.assertRaises(AccessError):
            request.with_user(self.manager_user).write(
                {"requested_by": self.manager_user.id}
            )

    def test_sales_rep_partner_immutable(self):
        request = self._create_field_update(self.customer_c1)
        with self.assertRaises(AccessError):
            request.with_user(self.manager_user).write(
                {"sales_rep_partner_id": self.agent_a2.id}
            )

    # ------------------------------------------------------------------
    # Direct state write bypass
    # ------------------------------------------------------------------

    def test_direct_state_write_blocked(self):
        request = self._create_field_update(self.customer_c1)
        with self.assertRaises(AccessError):
            request.with_user(self.manager_user).write({"state": "approved"})

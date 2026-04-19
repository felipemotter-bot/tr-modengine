# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import SalesRepAccessTestCommon


@tagged("post_install", "-at_install")
class TestRepPartnerViews(SalesRepAccessTestCommon):
    """Coverage for PR 5: server-side hide of non-operational sensitive
    fields on ``res.partner`` for the sales rep group.

    The PR originally scoped three buckets — server-side hide
    (non-operational), view-only hide (fiscal operational), and
    readonly-active (cadastral UX). The two view-only buckets hit a
    hard limitation of ``xpath position="attributes"`` in Odoo 16:
    the override only applies to the first match, and the partner
    form declares the affected fields in multiple subviews (kanban,
    child_ids subtree). A partial hide/readonly would be UI theater.

    Both Felipe and Codex agreed (2026-04-19) to keep this PR focused
    on bucket A and move the other two to dedicated PRs with a
    different approach. The real protection on the cadastral fields
    already lives in PR 4b's ``res.partner.write`` guard — the UI
    readonly is a polish layer we can add later.
    """

    def test_rep_cannot_read_capital_amount(self):
        with self.assertRaises(AccessError):
            self.customer_c1.with_user(self.user_u1).read(["capital_amount"])

    def test_rep_cannot_read_turnover_range(self):
        with self.assertRaises(AccessError):
            self.customer_c1.with_user(self.user_u1).read(["turnover_range_id"])

    def test_rep_cannot_read_wh_cityhall(self):
        with self.assertRaises(AccessError):
            self.customer_c1.with_user(self.user_u1).read(["wh_cityhall"])

    def test_rep_cannot_read_is_accountant(self):
        with self.assertRaises(AccessError):
            self.customer_c1.with_user(self.user_u1).read(["is_accountant"])

    def test_rep_cannot_write_capital_amount(self):
        with self.assertRaises(AccessError):
            self.customer_c1.with_user(self.user_u1).write({"capital_amount": 1000.0})

    def test_rep_fields_get_does_not_expose_hidden(self):
        fields_info = self.env["res.partner"].with_user(self.user_u1).fields_get()
        for hidden in (
            "capital_amount",
            "capital_currency_id",
            "turnover_range_id",
            "turnover_amount",
            "company_size",
            "is_accountant",
            "crc_code",
            "crc_state_id",
            "wh_cityhall",
            "union_entity_code",
        ):
            self.assertNotIn(
                hidden,
                fields_info,
                "Field %s must be hidden from fields_get for reps." % hidden,
            )

    def test_manager_can_read_hidden_fields(self):
        admin = self.env.ref("base.user_admin")
        result = self.customer_c1.with_user(admin).read(
            ["capital_amount", "wh_cityhall", "is_accountant"]
        )
        self.assertTrue(result, "Admin should read every hidden field.")

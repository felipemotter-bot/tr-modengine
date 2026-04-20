# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest import mock

from lxml import etree

from odoo.addons.tr_pricelist_report.models.res_partner import (
    ResPartner as UpstreamPricelistPartner,
)

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import SalesRepAccessTestCommon


@tagged("post_install", "-at_install")
class TestRepPricelistReportBlock(SalesRepAccessTestCommon):
    """Coverage for PR 7 commit 3 — rep is blocked from the
    ``tr_pricelist_report`` wizard via server-side guards plus
    view-level button hiding as defense in depth.

    The wizard itself would otherwise expose products outside the
    rep's allowed catalog (PR 2 scope). The server-side guard is
    the real stop — the view hide only keeps the UI clean, since
    the upstream module also exposes the action through an
    ``ir.actions.server`` binding on ``res.partner`` that shows up
    in the list/form Action menu.
    """

    def test_rep_cannot_call_action_print_pricelist(self):
        with self.assertRaises(AccessError):
            self.customer_c1.with_user(self.user_u1).action_print_pricelist()

    def test_rep_cannot_call_action_print_pricelist_from_menu(self):
        with self.assertRaises(AccessError):
            self.customer_c1.with_user(self.user_u1).action_print_pricelist_from_menu()

    def test_admin_delegates_to_upstream_action_print_pricelist(self):
        # Regression: the rep-only guard must not intercept the
        # admin path; the override should delegate straight to the
        # upstream ``tr_pricelist_report`` implementation. Patch
        # the upstream class method so the test does not depend on
        # a commercial condition fixture — we only care that the
        # admin call reaches super() and receives the upstream
        # return value unchanged.
        admin = self.env.ref("base.user_admin")
        sentinel = {"type": "ir.actions.act_window", "_probe": True}
        with mock.patch.object(
            UpstreamPricelistPartner,
            "action_print_pricelist",
            return_value=sentinel,
        ):
            result = self.customer_c1.with_user(admin).action_print_pricelist()
        self.assertEqual(
            result,
            sentinel,
            "Admin call must reach super() without the rep guard; "
            "expected the patched upstream return value.",
        )

    def test_rep_sale_order_form_hides_print_pricelist_button(self):
        # Defense in depth: the rep's resolved partner form must
        # not contain the "Print Price List" button at all.
        arch = etree.fromstring(
            self.env["res.partner"]
            .with_user(self.user_u1)
            .fields_view_get(view_type="form")["arch"]
        )
        nodes = arch.xpath("//button[@name='action_print_pricelist']")
        self.assertFalse(
            nodes,
            "The Print Price List button must be hidden from the "
            "rep on the partner form — field-level groups= filters "
            "it out.",
        )

    def test_admin_partner_form_keeps_print_pricelist_button(self):
        # Regression: admin still sees the button.
        admin = self.env.ref("base.user_admin")
        arch = etree.fromstring(
            self.env["res.partner"]
            .with_user(admin)
            .fields_view_get(view_type="form")["arch"]
        )
        self.assertTrue(
            arch.xpath("//button[@name='action_print_pricelist']"),
            "Admin must still see the Print Price List button.",
        )

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import SalesRepAccessTestCommon

SENSITIVE_FIELDS = (
    "last_order_id",
    "last_order_date",
    "last_order_status",
    "order_count",
    "total_ordered",
    "average_ordered",
    "average_ordered_no_discrepancies",
    "average_time_between_orders",
    "days_since_last_order",
    "last_invoice_date",
    "invoice_count",
    "total_invoiced",
    "average_invoiced",
    "average_invoiced_no_discrepancies",
    "average_time_between_invoices",
    "last_invoice_id",
    "days_since_last_invoice",
    "analysis_message",
    "has_open_quotation",
    "last_open_quotation_id",
    "last_open_quotation_date",
)


@tagged("post_install", "-at_install")
class TestRepPartnerSalesInfo(SalesRepAccessTestCommon):
    """Coverage for the PR 7 commit 1 server-side hide of
    ``eng_partner_sales_info`` aggregated sales statistics on
    ``res.partner``.

    The upstream module hides the analysis tab in the form via
    ``groups="eng_partner_sales_info.group_partner_sales_analysis"``
    but leaves the field declarations open on the Python side, so
    any RPC caller can pull the values. PR 7 redeclares each field
    with ``groups="!tr_sales_rep_access.group_sales_rep_external"``
    to block the rep at the ORM edge (read/fields_get/search).
    """

    def test_rep_cannot_read_sensitive_sales_info_fields(self):
        for fname in SENSITIVE_FIELDS:
            with self.assertRaises(
                AccessError,
                msg=(
                    f"Rep must not be able to read {fname} via RPC "
                    f"— the field declaration should exclude the "
                    f"rep group"
                ),
            ):
                self.customer_c1.with_user(self.user_u1).read([fname])

    def test_rep_fields_get_filters_sensitive_sales_info(self):
        # ``fields_get`` is the introspection entry point used by
        # the web client to discover which fields exist. The ORM
        # filters out fields the current user has no group for —
        # so none of the sensitive fields should appear for the rep.
        info = self.env["res.partner"].with_user(self.user_u1).fields_get()
        leaked = [f for f in SENSITIVE_FIELDS if f in info]
        self.assertFalse(
            leaked,
            f"fields_get must not expose the sales-info fields for "
            f"the rep, but leaked: {leaked}",
        )

    def test_admin_can_still_read_sales_info_fields(self):
        # Regression: the rep-only exclusion must not break admin
        # workflows. Admin keeps ORM access to every field.
        values = self.customer_c1.sudo().read(list(SENSITIVE_FIELDS))
        self.assertEqual(len(values), 1)
        # Smoke-check two scalar fields to prove the read succeeded
        # without raising; values themselves depend on the fixture
        # and are irrelevant here.
        self.assertIn("order_count", values[0])
        self.assertIn("total_ordered", values[0])

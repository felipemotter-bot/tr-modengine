# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models

REP_GROUP_XMLID = "tr_sales_rep_access.group_sales_rep_external"


class ResPartner(models.Model):
    _inherit = "res.partner"

    # PR 7 commit 1 — promote the rep-exclusion `groups=` from the view
    # (where ``eng_partner_sales_info`` already restricts the analysis
    # tab to ``group_partner_sales_analysis``) to the Python field
    # declaration. Without this, a rep could read the aggregated
    # sales stats via RPC (``read``/``fields_get``/search) even
    # though the form hides them. DA-6 of the plano applies: never
    # trust view-level ``groups=`` as security.

    last_order_id = fields.Many2one(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    last_order_date = fields.Date(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    last_order_status = fields.Char(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    order_count = fields.Integer(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    total_ordered = fields.Monetary(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    average_ordered = fields.Monetary(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    average_ordered_no_discrepancies = fields.Monetary(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    average_time_between_orders = fields.Float(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    days_since_last_order = fields.Integer(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    last_invoice_date = fields.Date(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    invoice_count = fields.Integer(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    total_invoiced = fields.Monetary(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    average_invoiced = fields.Monetary(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    average_invoiced_no_discrepancies = fields.Monetary(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    average_time_between_invoices = fields.Float(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    last_invoice_id = fields.Many2one(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    days_since_last_invoice = fields.Integer(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    analysis_message = fields.Text(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    has_open_quotation = fields.Boolean(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    last_open_quotation_id = fields.Many2one(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    last_open_quotation_date = fields.Date(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )

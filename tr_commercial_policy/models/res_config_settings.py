# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    company_default_sales_profile_id = fields.Many2one(
        related="company_id.default_sales_profile_id",
        readonly=False,
    )
    company_invoice_validity_days = fields.Integer(
        related="company_id.invoice_validity_days",
        readonly=False,
    )
    tr_policy_tax_rate = fields.Float(
        string="Tax Rate (%)",
        config_parameter="tr_commercial_policy.tax_rate_pct",
        default=0.0,
        help=(
            "Composite tax rate used to compute the contractual return"
            " adjustment factor. Changes affect only new documents"
            " and drafts that are recomputed after the change — existing"
            " drafts keep their current factor until touched."
        ),
    )
    tr_policy_freight_rate = fields.Float(
        string="Freight Rate (%)",
        config_parameter="tr_commercial_policy.freight_rate_pct",
        default=0.0,
        help=(
            "Freight rate used to compute the contractual return"
            " adjustment factor. Changes affect only new documents"
            " and drafts that are recomputed after the change — existing"
            " drafts keep their current factor until touched."
        ),
    )
    tr_policy_admin_rate = fields.Float(
        string="Administrative Expense Rate (%)",
        config_parameter="tr_commercial_policy.admin_rate_pct",
        default=0.0,
        help=(
            "Administrative expense rate used to compute the contractual"
            " return adjustment factor. Composes additively with tax and"
            " freight as a deduction from the gross price. Changes affect"
            " only new documents and drafts that are recomputed after"
            " the change — existing drafts keep their current factor"
            " until touched."
        ),
    )

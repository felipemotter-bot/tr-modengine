# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = "res.company"

    default_sales_profile_id = fields.Many2one(
        comodel_name="tr.sales.profile",
    )
    invoice_validity_days = fields.Integer(
        string="Invoice Validity (days)",
        default=0,
        help="Maximum days after sale order confirmation for invoice to"
        " retain parity. 0 = disabled.",
    )

    @api.constrains("invoice_validity_days")
    def _check_invoice_validity_days(self):
        for company in self:
            if company.invoice_validity_days < 0:
                raise ValidationError(_("Invoice validity days cannot be negative."))

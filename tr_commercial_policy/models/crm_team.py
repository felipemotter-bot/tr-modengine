# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class CrmTeam(models.Model):
    _inherit = "crm.team"

    sales_profile_id = fields.Many2one(
        comodel_name="tr.sales.profile",
        string="Sales Profile",
        check_company=True,
        help="Default sales profile for members of this team. "
        "Individual profile on the salesperson takes precedence.",
    )

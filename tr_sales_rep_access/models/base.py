# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, models
from odoo.exceptions import AccessError


class Base(models.AbstractModel):
    _inherit = "base"

    def export_data(self, fields_to_export):
        if self.env.user.has_group("tr_sales_rep_access.group_sales_rep_external"):
            raise AccessError(_("Sales rep users are not allowed to export data."))
        return super().export_data(fields_to_export)

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, models

REP_GROUP_XMLID = "tr_sales_rep_access.group_sales_rep_external"


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model
    def review_user_count(self):
        if self.env.user.has_group(REP_GROUP_XMLID):
            return []
        return super().review_user_count()

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, models


class PricelistReport(models.AbstractModel):
    _name = "report.tr_pricelist_report.report_pricelist_document"
    _description = "Pricelist Report"

    @api.model
    def _get_report_values(self, docids, data=None):
        wizard_model = self.env["tr.pricelist.report.wizard"]
        return wizard_model._get_report_values(docids, data=data)

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


class PricelistBasicReport(models.AbstractModel):
    """Adapter so the QWeb engine pulls the Basic wizard's payload.

    ``ir.actions.report._get_rendering_context`` resolves
    ``report.<report_name>`` and calls ``_get_report_values`` on it.
    Without this adapter the engine falls back to the generic one that
    only injects ``doc_ids``/``doc_model``/``docs``, and the template's
    extra vars (``pricelist``, ``sections``, ...) would be undefined.
    """

    _name = "report.tr_pricelist_report.report_pricelist_basic_document"
    _description = "Basic Pricelist Report"

    @api.model
    def _get_report_values(self, docids, data=None):
        wizard_model = self.env["tr.pricelist.basic.wizard"]
        return wizard_model._get_report_values(docids, data=data)

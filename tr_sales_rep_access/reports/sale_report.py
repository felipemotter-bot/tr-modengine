# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, models
from odoo.exceptions import UserError

CONFERENCE_TIER_XMLID = "tr_sales_rep_access.tier_def_sales_rep_order_conference"


class ReportSaleOrder(models.AbstractModel):
    _inherit = "report.sale.report_saleorder"

    @api.model
    def _get_report_values(self, docids, data=None):
        # PR 6b — relax the ``sale_tier_validation`` print block for
        # orders whose only pending review is the "Sales rep order
        # conference" tier. That tier is operational (stock, fiscal,
        # timing) and does not commit the company to a price, so
        # letting the rep print the quotation while the checker's
        # review is still pending keeps the negotiation flowing.
        # Manager / Director discount tiers, when still pending,
        # keep the block — printing with an unapproved discount
        # would bind the company to a value it did not agree to.
        #
        # We cannot call ``super()._get_report_values()`` in the
        # "conference-only pending" path because the upstream check
        # re-raises for ``need_validation`` / ``validation_status
        # != 'validated'``. We return the report dict directly with
        # the same shape the core's
        # ``report.sale.report_saleorder`` emits. If a future
        # module in the chain starts enriching that dict, this
        # override would drop that enrichment — low risk today
        # (upstream has the 4 canonical keys and nothing else).
        conference_tier = self.env.ref(CONFERENCE_TIER_XMLID, raise_if_not_found=False)
        docs = self.env["sale.order"].browse(docids)
        for order in docs:
            if not order.company_id.sale_report_print_block:
                continue
            if order.validation_status == "validated":
                continue
            pending = order.review_ids.filtered(lambda r: r.status != "approved")
            if not pending:
                continue
            if conference_tier and not any(
                review.definition_id != conference_tier for review in pending
            ):
                # All pending reviews belong to the conference tier —
                # let this order through without calling super().
                continue
            raise UserError(
                _("Quotation printing is blocked until the order is approved.")
            )
        # All orders cleared (validated, conference-only pending, or
        # company block disabled). Return the report dict directly —
        # bypassing super() is intentional; see comment above.
        return {
            "doc_ids": docids,
            "doc_model": "sale.order",
            "docs": docs,
            "data": data,
        }

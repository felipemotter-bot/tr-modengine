# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    discount = fields.Float(
        compute="_compute_tr_discount",
        store=True,
    )
    discount_value = fields.Monetary(
        compute="_compute_tr_discount",
        store=True,
    )

    @api.depends(
        "quantity",
        "price_unit",
        "fiscal_document_line_id",
        "fiscal_document_line_id.discount_value",
        "move_id.tr_cash_discount",
        "move_id.tr_fob_discount",
        "move_id.state",
    )
    def _compute_tr_discount(self):
        """Resolve ``discount`` e ``discount_value`` da linha da fatura.

        Bifurca por ``fiscal_document_line_id``:
        - com fiscal: documento fiscal manda — preserva fluxo NF-e/SPED.
        - sem fiscal: aplica ``tr_cash_discount + tr_fob_discount`` do
          cabeçalho da fatura (política comercial).

        ``state == 'posted'`` é gate imutável: protege histórico contra o
        recompute em massa do ORM no install do módulo.

        Sem política no header, preserva o ``discount`` existente como
        percentual efetivo, mas SEMPRE recalcula ``discount_value`` da
        base atual para evitar drift entre percentual e valor monetário.
        """
        for line in self:
            if line.move_id.state == "posted":
                continue
            base = (line.quantity or 0.0) * (line.price_unit or 0.0)
            if line.fiscal_document_line_id:
                fiscal_dv = line.fiscal_document_line_id.discount_value or 0.0
                line.discount_value = fiscal_dv
                line.discount = (fiscal_dv * 100.0 / base) if base else 0.0
                continue
            pct = (line.move_id.tr_cash_discount or 0.0) + (
                line.move_id.tr_fob_discount or 0.0
            )
            effective_pct = pct if pct else (line.discount or 0.0)
            line.discount = effective_pct
            line.discount_value = base * effective_pct / 100.0

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
        "move_id.tr_cash_discount",
        "move_id.tr_fob_discount",
        "move_id.state",
    )
    def _compute_tr_discount(self):
        """Resolve ``discount`` e ``discount_value`` a partir do cabecalho.

        Header da fatura (``tr_cash_discount + tr_fob_discount``) e a
        fonte unica do percentual aplicado. ``discount_value`` deriva da
        base atual da linha (``quantity * price_unit``).

        ``state == 'posted'`` e gate imutavel: protege historico contra
        o recompute em massa do ORM no install do modulo.

        Em ``l10n_br_fiscal.document.line``, ``discount_value`` e
        redefinido como ``related("account_line_ids.discount_value")``,
        de forma que a NF-e/SPED leia o valor que a linha do account
        guarda. Mesmo padrao que o ``engenere_account_invoice_br_discount``
        usava em producao por anos.
        """
        for line in self:
            if line.move_id.state == "posted":
                continue
            base = (line.quantity or 0.0) * (line.price_unit or 0.0)
            pct = (line.move_id.tr_cash_discount or 0.0) + (
                line.move_id.tr_fob_discount or 0.0
            )
            line.discount = pct
            line.discount_value = base * pct / 100.0

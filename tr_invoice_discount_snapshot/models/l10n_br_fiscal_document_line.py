# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class L10nBrFiscalDocumentLine(models.Model):
    _inherit = "l10n_br_fiscal.document.line"

    # Inverte a fonte de verdade do desconto: a ``account.move.line`` passa
    # a ditar o valor; a linha fiscal le via related. Mesmo padrao do
    # ``engenere_account_invoice_br_discount`` (descontinuado), que rodou
    # em producao por anos sem incidente. Vantagens:
    #
    # - Editar ``tr_cash_discount + tr_fob_discount`` no header da fatura
    #   recalcula o ``account.move.line.discount_value``, e a NF-e ve o
    #   mesmo valor automaticamente.
    # - Mudar ``quantity`` ou ``price_unit`` em draft mantem o percentual
    #   estavel (compute na account.move.line recalcula a base).
    # - Sem write-hook imperativo, sem snapshot duplicado, sem guard de
    #   recursao.
    #
    # Risco conhecido: ``account_line_ids`` e One2many; ``related`` escalar
    # le o primeiro registro. Em invoice grouping com varias account lines
    # por fiscal line (raro/inexistente no fluxo Trento), so a primeira
    # contribui. Acompanhado por testes.
    discount_value = fields.Monetary(related="account_line_ids.discount_value")

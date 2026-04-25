# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

_logger = logging.getLogger(__name__)


def pre_init_hook(cr):
    """Snapshot de ``discount`` e ``discount_value`` antes do recompute do
    ORM no install.

    Quando este módulo é instalado, ele redefine ``account.move.line.discount``
    e ``account.move.line.discount_value`` com computes próprios. O ORM
    marca todas as linhas para recompute. O gate ``state == 'posted'`` no
    compute protege o histórico, mas como dado fiscal é crítico, gravamos
    um snapshot SQL antes do install para que ``post_init_hook`` possa
    restaurar qualquer linha posted que tenha sido alterada.

    Pré-cria também ``discount_value`` como coluna stored: em prod e devel
    reais ela existe (resíduo do ``engenere_account_invoice_br_discount``
    desinstalado), mas em bases zeradas (ex: ``devel_test``) não. Pré-criar
    aqui evita o ``UPDATE`` falhar com ``UndefinedColumn`` e também ensina
    o ORM que o campo redefinido pelo módulo já tem a coluna no lugar.
    """
    cr.execute(
        """
        ALTER TABLE account_move_line
        ADD COLUMN IF NOT EXISTS discount_value NUMERIC,
        ADD COLUMN IF NOT EXISTS tr_discount_backup NUMERIC,
        ADD COLUMN IF NOT EXISTS tr_discount_value_backup NUMERIC
        """
    )
    cr.execute(
        """
        UPDATE account_move_line
        SET tr_discount_backup = discount,
            tr_discount_value_backup = discount_value
        WHERE tr_discount_backup IS NULL OR tr_discount_value_backup IS NULL
        """
    )
    _logger.info("tr_invoice_discount_snapshot: snapshot pre-install gravado.")


def post_init_hook(cr, registry):
    """Restaura snapshot em faturas posted onde o recompute alterou valores.

    Defesa contra qualquer escape do gate ``state == 'posted'`` no compute.
    Idempotente: roda só onde discount/discount_value diferem do snapshot.
    """
    cr.execute(
        """
        UPDATE account_move_line aml
        SET discount = aml.tr_discount_backup,
            discount_value = aml.tr_discount_value_backup
        FROM account_move am
        WHERE aml.move_id = am.id
          AND am.state = 'posted'
          AND (
              aml.discount IS DISTINCT FROM aml.tr_discount_backup
              OR aml.discount_value IS DISTINCT FROM aml.tr_discount_value_backup
          )
        """
    )
    restored = cr.rowcount or 0
    if restored:
        _logger.warning(
            "tr_invoice_discount_snapshot: restauradas %d linhas posted "
            "alteradas pelo recompute (snapshot pre-install).",
            restored,
        )
    else:
        _logger.info(
            "tr_invoice_discount_snapshot: nenhuma linha posted alterada "
            "pelo recompute, snapshot intacto."
        )

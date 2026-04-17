# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from openupgradelib import openupgrade

_logger = logging.getLogger(__name__)


@openupgrade.migrate()
def migrate(env, version):
    """Repair orders whose computed snapshot was wiped by the 16.0.2.1.0
    pre_init_hook type mismatch.

    The previous hook created the columns below as ``NUMERIC`` but the
    matching ``fields.Float`` definitions have no ``digits=`` attribute,
    so on the first ORM load the columns were dropped and recreated as
    ``DOUBLE PRECISION``, wiping any previously persisted values:

    - ``sale_order.contractual_return``
    - ``sale_order.punctuality_discount``
    - ``sale_order_line.adjustment_factor``

    The wipe also corrupted downstream snapshots that survived at the
    schema level (``reference_price``, ``price_unit`` on lines) because
    they were re-persisted during the window using ``contractual_return``
    read as NULL → 0, producing a ``reference_price`` equal to
    ``base_price`` with no contractual-return adjustment. That broken
    snapshot leaks into invoices via ``_prepare_invoice_line``, so the
    fix recomputes the whole line pricing chain for the affected orders.

    Scope of the repair is restricted to the objectively corrupted
    population — ``contractual_return IS NULL`` with a condition set —
    so legitimate historical snapshots on other orders stay untouched.
    """
    # 1. Identify corrupted orders: column was wiped AND a condition is
    # linked (so the repair has a source of truth).
    env.cr.execute(
        """
        SELECT so.id
        FROM sale_order so
        JOIN partner_commercial_condition pc
            ON pc.id = so.commercial_condition_id
        WHERE so.contractual_return IS NULL
          AND pc.contractual_return IS NOT NULL
        """
    )
    corrupted_ids = [row[0] for row in env.cr.fetchall()]
    if not corrupted_ids:
        return

    # 2. Repair contractual_return from the linked condition.
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE sale_order so
        SET contractual_return = pc.contractual_return
        FROM partner_commercial_condition pc
        WHERE so.commercial_condition_id = pc.id
          AND so.id IN %s
        """,
        (tuple(corrupted_ids),),
    )

    # 3. Repair punctuality_discount via direct SQL. The compute exits
    # early for sale/done without honoring force_policy_recompute, so a
    # compute call would no-op for confirmed orders.
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE sale_order
        SET punctuality_discount = COALESCE(contractual_return, 0.0)
        WHERE id IN %s
        """,
        (tuple(corrupted_ids),),
    )

    # 4. Recompute the line pricing chain (adjustment_factor,
    # reference_price, price_unit, discount, discount_value) for the
    # affected orders. These snapshots were persisted with
    # contractual_return=NULL→0 and are semantically wrong; leaving
    # them stale would contaminate future invoices via
    # _prepare_invoice_line.
    #
    # NOTE: ``_compute_price_unit`` deliberately skips lines with
    # ``qty_invoiced > 0`` because rewriting price_unit on an already
    # invoiced line would create a silent accounting divergence
    # (sale.amount vs invoice.amount) without fixing the issued invoice.
    # Those lines are reported below for manual follow-up (credit note
    # + re-invoicing, or accept as consumed damage) — the migration
    # is not the right place to force that decision.
    env["sale.order"].flush()
    env["sale.order.line"].invalidate_model(
        [
            "adjustment_factor",
            "reference_price",
            "price_unit",
            "discount",
            "discount_value",
        ]
    )
    orders = env["sale.order"].browse(corrupted_ids)
    lines = orders.order_line.filtered("product_id")
    skipped = lines.filtered(lambda sol: sol.qty_invoiced > 0)
    if skipped:
        _logger.warning(
            "Migration 16.0.2.2.0: %d line(s) with qty_invoiced > 0 left "
            "with stale price_unit; manual reconciliation required "
            "(order ids: %s).",
            len(skipped),
            sorted({line.order_id.id for line in skipped}),
        )
    lines.with_context(force_policy_recompute=True)._compute_reference_price()
    lines.with_context(force_policy_recompute=True)._compute_price_unit()
    lines._compute_discounts()

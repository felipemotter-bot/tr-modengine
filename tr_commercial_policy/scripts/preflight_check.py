#!/usr/bin/env python3
# pylint: disable=print-used,sql-injection
"""Pre-installation check for tr_commercial_policy.

Reports data that may cause issues during module installation.
Run BEFORE installing the module, using the Odoo shell.

Doodba (this repo):
    docker compose run --rm odoo odoo shell -d <database> \
        < odoo/custom/src/tr-modengine/tr_commercial_policy/scripts/preflight_check.py

Direct:
    odoo shell -d <database> < tr_commercial_policy/scripts/preflight_check.py
"""

import sys

cr = env.cr  # noqa: F821 — provided by odoo shell

print("=== Preflight check ===\n")
ok = True

# 1. Volume
print("--- Record volume ---")
for table, label in [
    ("sale_order", "Sale Orders"),
    ("sale_order_line", "Sale Order Lines"),
    ("account_move", "Account Moves"),
    ("account_move_line", "Account Move Lines"),
]:
    cr.execute(f"SELECT count(*) FROM {table}")  # noqa: S608
    count = cr.fetchone()[0]
    print(f"  {label}: {count:,}")

# 2. Orphan agents
print("\n--- Orphan agent records ---")
cr.execute(
    """
    SELECT count(*) FROM sale_order_line_agent a
    WHERE NOT EXISTS (
        SELECT 1 FROM sale_order_line l WHERE l.id = a.object_id
    )
    """
)
orphan_sol_agents = cr.fetchone()[0]
print(f"  sale.order.line.agent orphans: {orphan_sol_agents}")
if orphan_sol_agents:
    ok = False

cr.execute(
    """
    SELECT count(*) FROM account_invoice_line_agent a
    WHERE NOT EXISTS (
        SELECT 1 FROM account_move_line l WHERE l.id = a.object_id
    )
    """
)
orphan_aml_agents = cr.fetchone()[0]
print(f"  account.invoice.line.agent orphans: {orphan_aml_agents}")
if orphan_aml_agents:
    ok = False

# 3. Orphan tier reviews
print("\n--- Orphan tier reviews ---")
for model, table in [
    ("sale.order", "sale_order"),
    ("account.move", "account_move"),
]:
    cr.execute(
        """
        SELECT count(*) FROM tier_review tr
        WHERE tr.model = %s
          AND NOT EXISTS (
              SELECT 1 FROM {} t WHERE t.id = tr.res_id
          )
        """.format(table),
        (model,),
    )
    orphans = cr.fetchone()[0]
    print(f"  {model} orphan reviews: {orphans}")
    if orphans:
        ok = False

# 4. Partners needing commercial conditions
print("\n--- Partners needing conditions ---")
cr.execute(
    """
    SELECT count(DISTINCT rp.id)
    FROM res_partner rp
    INNER JOIN sale_order so
        ON so.partner_id = rp.id AND so.state IN ('sale', 'done')
    LEFT JOIN ir_property ip
        ON ip.res_id = CONCAT('res.partner,', rp.id::text)
        AND ip.name = 'commercial_condition_id'
        AND ip.value_reference IS NOT NULL
    WHERE ip.id IS NULL
      AND rp.is_company = True
    """
)
partners = cr.fetchone()[0]
print(f"  Partners with sales but no condition: {partners}")

# 5. Columns already present
print("\n--- Pre-existing columns (from prior install attempt) ---")
for table, col in [
    ("sale_order", "sales_profile_id"),
    ("sale_order", "commercial_condition_id"),
    ("sale_order_line", "base_price"),
    ("account_move", "discount_approval_level"),
]:
    cr.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
        """,
        (table, col),
    )
    exists = "YES" if cr.fetchone() else "no"
    print(f"  {table}.{col}: {exists}")

# Summary
print("\n" + "=" * 40)
if ok:
    print("OK - no structural issues found. Safe to install.")
else:
    print(
        "WARNING - orphan records found. The pre_init_hook will clean "
        "them automatically, but review the counts above."
    )

cr.rollback()
sys.exit(0 if ok else 1)

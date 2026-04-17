# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    openupgrade.logged_query(
        env.cr,
        """
        ALTER TABLE partner_commercial_condition_line
        ADD COLUMN IF NOT EXISTS applied_on VARCHAR
        """,
    )
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE partner_commercial_condition_line
        SET applied_on = 'product'
        WHERE product_id IS NOT NULL
        """,
    )
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE partner_commercial_condition_line
        SET applied_on = 'product_template'
        WHERE applied_on IS NULL
        """,
    )
    # Clear product_tmpl_id on variant lines (applied_on = 'product')
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE partner_commercial_condition_line
        SET product_tmpl_id = NULL
        WHERE applied_on = 'product'
        """,
    )

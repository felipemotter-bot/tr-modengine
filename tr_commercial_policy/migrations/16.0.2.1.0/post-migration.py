# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    """Sanitize existing sales profiles for the new at-least-one-rule
    constraint introduced in 16.0.2.1.0.
    """
    from ...hooks import ensure_profiles_have_rules

    ensure_profiles_have_rules(env)

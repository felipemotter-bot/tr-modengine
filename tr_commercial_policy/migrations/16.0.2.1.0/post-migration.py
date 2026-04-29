# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from openupgradelib import openupgrade

_logger = logging.getLogger(__name__)


# Keep this migration self-contained: the original helper lived in
# hooks.py, which was later removed because install hooks were
# one-shot bootstrap code. The placeholder rule values are inlined
# here verbatim from the original implementation so this migration
# keeps working if it ever runs again on an older DB.
_PLACEHOLDER_SEQUENCE = 999

_AGENT_PLACEHOLDER_RULE = [
    (
        0,
        0,
        {
            "applied_on": "general",
            "sequence": _PLACEHOLDER_SEQUENCE,
            "commission_band_ids": [
                (0, 0, {"discount_up_to": 100.0, "commission_rate": 0.0}),
            ],
        },
    ),
]

_INTERNAL_PLACEHOLDER_RULE = [
    (
        0,
        0,
        {
            "applied_on": "general",
            "sequence": _PLACEHOLDER_SEQUENCE,
            "order_value_band_ids": [
                (0, 0, {"order_min_amount": 0.0, "seller_discount_max": 100.0}),
            ],
        },
    ),
]


@openupgrade.migrate()
def migrate(env, version):
    """Sanitize existing sales profiles for the new at-least-one-rule
    constraint introduced in 16.0.2.1.0.
    """
    Profile = env["tr.sales.profile"]
    profiles_without_rules = Profile.search([("rule_ids", "=", False)])
    for profile in profiles_without_rules:
        if profile.profile_type == "agent":
            profile.write({"rule_ids": _AGENT_PLACEHOLDER_RULE})
        else:
            profile.write({"rule_ids": _INTERNAL_PLACEHOLDER_RULE})
        _logger.info(
            "Added placeholder rule to profile '%s' (id=%d).",
            profile.name,
            profile.id,
        )

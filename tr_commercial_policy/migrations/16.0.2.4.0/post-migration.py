# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from openupgradelib import openupgrade

from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_MAX_DETAILS = 50


@openupgrade.migrate()
def migrate(env, version):
    """Recompute ``applicable_profile_id`` of every commercial condition
    using the new company-aware resolver. Abort the upgrade with an
    explicit error listing conditions that ended up unresolved or
    cross-company so the operator fixes the underlying setup
    (agent/team/default profile per company) before retrying.

    The constraint introduced in 16.0.2.4.0 raises on these states for
    new writes; the migration uses
    ``skip_profile_resolution_check=True`` to defer validation to a
    single aggregated pass after the bulk recompute. Without this,
    ``flush_recordset`` would raise per-record on the first invalid
    condition and the operator would only see one entry instead of the
    full diagnostic list.
    """
    Condition = env["partner.commercial.condition"].with_context(
        skip_profile_resolution_check=True
    )
    conditions = Condition.search([])
    _logger.info(
        "Recomputing applicable_profile_id for %d commercial condition(s)...",
        len(conditions),
    )
    conditions._compute_applicable_profile()
    conditions.flush_recordset(["applicable_profile_id", "profile_source"])

    unresolved = conditions.filtered(lambda c: not c.applicable_profile_id)
    cross_company = conditions.filtered(
        lambda c: c.applicable_profile_id
        and c.applicable_profile_id.company_id != c.company_id
    )
    problems = unresolved | cross_company
    if not problems:
        _logger.info("All commercial conditions resolved cleanly.")
        return

    sample = problems[:_MAX_DETAILS]
    extra = len(problems) - len(sample)
    details = "\n".join(
        f"- {c.partner_id.display_name} (company {c.company_id.name})" for c in sample
    )
    suffix = f"\n... and {extra} more" if extra > 0 else ""
    raise UserError(
        _(
            "Could not resolve a same-company sales profile for %(total)d "
            "commercial condition(s) after upgrade — %(unresolved)d without "
            "any profile and %(cross)d still pointing at another company's "
            "profile.\n\n%(details)s%(suffix)s\n\n"
            "Configure the agent/team/default sales profile for these "
            "customers in their respective companies and re-run the upgrade."
        )
        % {
            "total": len(problems),
            "unresolved": len(unresolved),
            "cross": len(cross_company),
            "details": details,
            "suffix": suffix,
        }
    )

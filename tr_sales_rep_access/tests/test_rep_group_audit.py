# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from .common import SalesRepAccessTestCommon


@tagged("post_install", "-at_install")
class TestRepGroupAudit(SalesRepAccessTestCommon):
    """PR 7 commit 4 — regression guards for group memberships that
    are load-bearing for the rep security model but easy to break
    silently.

    Only covers memberships that are stable data-declared state
    (not dependent on runtime fixtures). ``server_action_mass_edit``
    is deliberately out of scope here — its real risk depends on
    which ``ir.actions.server`` records are configured on the
    deployment, which is not something we can pin in a test. See
    ``AGENTS.md`` "Audit notes (PR 7)" for the full reasoning.
    """

    def test_rep_is_not_in_partner_sales_analysis_group(self):
        # ``eng_partner_sales_info.group_partner_sales_analysis``
        # gates the aggregated sales stats tab on the partner
        # form. The rep must never join that group; our PR 7
        # commit 1 field-level hide also covers the RPC path, but
        # this test fails fast if someone adds a transitive
        # ``implied_ids`` that would drag the rep in.
        analysis_group = self.env.ref(
            "eng_partner_sales_info.group_partner_sales_analysis"
        )
        self.assertNotIn(
            analysis_group,
            self.user_u1.groups_id,
            "Rep user must not be a member of "
            "eng_partner_sales_info.group_partner_sales_analysis — "
            "that group exposes aggregated sales statistics that "
            "PR 7 commit 1 hides at the field level.",
        )

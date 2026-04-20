# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, models
from odoo.exceptions import AccessError

REP_GROUP_XMLID = "tr_sales_rep_access.group_sales_rep_external"


class ResPartner(models.Model):
    _inherit = "res.partner"

    # PR 7 commit 3 — rep cannot generate the price list report.
    # Upstream ``tr_pricelist_report`` exposes two entrypoints on
    # the partner form: the "Print Price List" button (hidden from
    # the rep via the view override below) and a server-action
    # binding on ``res.partner`` that reaches
    # ``action_print_pricelist_from_menu``. The action menu is
    # data-declared and cannot be reliably hidden per-group on all
    # Odoo 16 clients, so we guard the method server-side here.
    # This also catches RPC callers that skip the UI altogether.
    #
    # Hiding both, but relying on the server-side gate for
    # security: the wizard would otherwise surface products outside
    # the rep's catalog (PR 2 scope), which is the actual concern.

    def action_print_pricelist(self):
        # Single chokepoint: ``action_print_pricelist_from_menu`` in
        # upstream already delegates to this method, so guarding
        # here covers both the header button and the action-menu
        # server binding.
        if self.env.user.has_group(REP_GROUP_XMLID):
            raise AccessError(
                _(
                    "Sales reps are not allowed to generate the partner "
                    "price list report — the wizard would expose products "
                    "outside the rep's allowed catalog."
                )
            )
        return super().action_print_pricelist()

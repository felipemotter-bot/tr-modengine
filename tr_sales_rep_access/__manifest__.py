# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Sales Rep Access",
    "version": "16.0.1.0.0",
    "maintainers": ["felipemotter"],
    "author": "Engenere",
    "license": "AGPL-3",
    "category": "Sales",
    "development_status": "Alpha",
    "summary": "External sales rep access: isolated group, snapshot-based "
    "record rules and export blocking.",
    "website": "https://github.com/Engenere/addons-trento",
    "depends": [
        "tr_commercial_policy",
        "partner_stage",
        "base_tier_validation",
        "partner_tier_validation",
        "sale_tier_validation",
        "sale",
        "account",
        "commission",
        # PR 5 — server-side hide (bucket A) redeclares fields from
        # these modules with ``groups=`` in ``models/res_partner.py``.
        # View-only hide of fiscal operational fields (``vat``,
        # ``tax_framework``, ``fiscal_profile_id``, ``ind_*``, etc.)
        # was deferred to PR 5b because XPath ``position=
        # "attributes"`` only applies to the first match, and those
        # fields appear in multiple subviews (kanban, child_ids
        # subtree) — partial hide would be security theater.
        "l10n_br_sped_base",
        "l10n_br_account_withholding",
        "l10n_br_hr",
        "partner_capital",
        # PR 7 commit 1 — server-side hide of the 21 aggregated
        # sales stats fields that ``eng_partner_sales_info`` adds
        # on ``res.partner``. View already restricts the tab, this
        # blocks direct RPC read/fields_get.
        "eng_partner_sales_info",
        # PR 7 commit 2 — hide the two widget fields that surface
        # ``sale_order_line_price_history``'s UI entrypoints to the
        # rep (the "show history" widget on the sale order line and
        # the "set price from history" widget inside the wizard).
        "sale_order_line_price_history",
        # PR 7 commit 3 — server-side block on
        # ``res.partner.action_print_pricelist`` /
        # ``action_print_pricelist_from_menu`` from
        # ``tr_pricelist_report`` so the rep cannot generate the
        # partner price list. View hide of the button is defense in
        # depth.
        "tr_pricelist_report",
        # PR 9 — redeclares qty_available/virtual_available_at_date etc.
        # with groups= to block RPC access for external reps.
        "sale_stock",
        # Field-level hides for One2many fields declared in these
        # modules (account_payment_ids, purchase_line_ids) and ACL
        # references on l10n_br_fiscal lookup models.
        "sale_advance_payment",
        "sale_purchase",
        "l10n_br_fiscal",
    ],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "security/ir_rules.xml",
        "data/tier_definition.xml",
        "views/tr_partner_change_request_views.xml",
        "views/sale_menus.xml",
        "views/sale_order_views.xml",
        "views/sale_order_line_price_history_views.xml",
        "views/tr_pricelist_report_views.xml",
        "views/account_move_views.xml",
        "views/res_partner_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "installable": True,
}

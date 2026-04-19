# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Sales Rep Access",
    "version": "16.0.1.0.0",
    "maintainers": ["felipemotter"],
    "author": "Engenere",
    "license": "AGPL-3",
    "category": "Sales",
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
    ],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "security/ir_rules.xml",
        "data/tier_definition.xml",
        "views/tr_partner_change_request_views.xml",
        "views/sale_order_views.xml",
        "views/res_partner_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "installable": True,
}

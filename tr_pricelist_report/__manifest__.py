# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Pricelist Report",
    "version": "16.0.1.0.0",
    "maintainers": ["felipemotter"],
    "author": "Engenere",
    "license": "AGPL-3",
    "category": "Sales",
    "summary": "Customer pricelist report driven by the commercial condition",
    "website": "https://github.com/Engenere/addons-trento",
    "depends": [
        "tr_commercial_policy",
        "product",
        "sale_management",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_config_parameter.xml",
        "data/disable_native_pricelist_reports.xml",
        "data/mail_template.xml",
        "wizards/pricelist_report_wizard_views.xml",
        "views/partner_commercial_condition_views.xml",
        "views/product_category_views.xml",
        "views/res_partner_views.xml",
        "report/pricelist_report.xml",
        "report/pricelist_report_templates.xml",
    ],
    "installable": True,
    "application": False,
    "post_init_hook": "post_init_hook",
}

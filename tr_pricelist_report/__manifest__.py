# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Pricelist Report",
    "version": "16.0.1.1.0",
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
        # partner_stage (OCA/partner-contact) adds the <header> with the
        # statusbar on res.partner.form. We target ``//header`` to drop
        # the Print Price List button next to the status, so the stage
        # module must be present for our view xpath to match.
        "partner_stage",
        # trento_report_utils provides the compact boxed layout
        # (`external_layout_boxed_compact`) + the low-margin A4
        # paperformat used across Trento reports. Our PDF template
        # t-calls that layout so it renders with the same company
        # header box as the rest of the stack.
        "trento_report_utils",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_config_parameter.xml",
        "data/disable_native_pricelist_reports.xml",
        "data/mail_template.xml",
        "wizards/pricelist_report_wizard_views.xml",
        "wizards/pricelist_basic_wizard_views.xml",
        "views/partner_commercial_condition_views.xml",
        "views/product_category_views.xml",
        "views/res_config_settings_views.xml",
        "views/res_partner_views.xml",
        "views/res_partner_server_action.xml",
        "report/pricelist_report.xml",
        "report/pricelist_basic_report.xml",
        "report/pricelist_report_styles.xml",
        "report/pricelist_report_templates.xml",
        "report/pricelist_basic_report_templates.xml",
    ],
    "installable": True,
    "application": False,
    "post_init_hook": "post_init_hook",
}

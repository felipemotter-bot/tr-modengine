# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

DEFAULT_CATALOG_PARAM = "tr_sales_rep_access.tr_sales_rep_default_category_ids"


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # Name does NOT start with ``default_`` because Odoo's settings
    # classifier would then demand a ``default_model`` attribute
    # (tied to ``ir.default``). This field is persisted manually into
    # ``ir.config_parameter`` via the overrides below.
    tr_sales_rep_default_category_ids = fields.Many2many(
        comodel_name="product.category",
        relation="tr_sales_rep_access_default_catalog_rel",
        column1="config_id",
        column2="category_id",
        string="Default Allowed Categories for New Agents",
        help=(
            "Categories assigned to the Allowed Product Categories "
            "of every new agent partner (agent=True) created without "
            "an explicit value. Does not retroactively update "
            "existing agents."
        ),
    )

    @api.model
    def get_values(self):
        res = super().get_values()
        raw = (
            self.env["ir.config_parameter"].sudo().get_param(DEFAULT_CATALOG_PARAM, "")
        )
        ids = []
        if raw:
            try:
                ids = [int(x) for x in raw.split(",") if x.strip()]
            except ValueError:
                ids = []
            Category = self.env["product.category"].sudo()
            ids = Category.browse(ids).exists().ids
        res["tr_sales_rep_default_category_ids"] = [(6, 0, ids)]
        return res

    def set_values(self):
        res = super().set_values()
        value = ",".join(str(i) for i in self.tr_sales_rep_default_category_ids.ids)
        self.env["ir.config_parameter"].sudo().set_param(DEFAULT_CATALOG_PARAM, value)
        return res

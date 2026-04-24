# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

DEFAULT_CATALOG_PARAM = "tr_sales_rep_access.tr_sales_rep_default_category_ids"
DEFAULT_PRICELIST_PARAM = "tr_sales_rep_access.tr_sales_rep_default_pricelist_ids"


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
    tr_sales_rep_default_pricelist_ids = fields.Many2many(
        comodel_name="product.pricelist",
        relation="tr_sales_rep_access_default_pricelist_rel",
        column1="config_id",
        column2="pricelist_id",
        string="Default Allowed Pricelists for New Agents",
        help=(
            "Pricelists assigned to the Allowed Pricelists of every "
            "new agent partner (agent=True) created without an "
            "explicit value. Also applied when an existing partner is "
            "flagged as agent (False → True) and has no pricelist "
            "whitelist set yet. Does not retroactively update "
            "previously flagged agents."
        ),
    )

    @api.model
    def get_values(self):
        res = super().get_values()
        ICP = self.env["ir.config_parameter"].sudo()
        raw_cat = ICP.get_param(DEFAULT_CATALOG_PARAM, "")
        cat_ids = []
        if raw_cat:
            try:
                cat_ids = [int(x) for x in raw_cat.split(",") if x.strip()]
            except ValueError:
                cat_ids = []
            Category = self.env["product.category"].sudo()
            cat_ids = Category.browse(cat_ids).exists().ids
        res["tr_sales_rep_default_category_ids"] = [(6, 0, cat_ids)]

        raw_pl = ICP.get_param(DEFAULT_PRICELIST_PARAM, "")
        pl_ids = []
        if raw_pl:
            try:
                pl_ids = [int(x) for x in raw_pl.split(",") if x.strip()]
            except ValueError:
                pl_ids = []
            Pricelist = self.env["product.pricelist"].sudo()
            pl_ids = Pricelist.browse(pl_ids).exists().ids
        res["tr_sales_rep_default_pricelist_ids"] = [(6, 0, pl_ids)]
        return res

    def set_values(self):
        res = super().set_values()
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param(
            DEFAULT_CATALOG_PARAM,
            ",".join(str(i) for i in self.tr_sales_rep_default_category_ids.ids),
        )
        ICP.set_param(
            DEFAULT_PRICELIST_PARAM,
            ",".join(str(i) for i in self.tr_sales_rep_default_pricelist_ids.ids),
        )
        return res

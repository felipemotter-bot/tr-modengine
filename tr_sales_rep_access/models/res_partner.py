# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

MANAGER_GROUP_XMLID = "tr_commercial_policy.group_sales_manager"
DEFAULT_CATALOG_PARAM = "tr_sales_rep_access.tr_sales_rep_default_category_ids"


class ResPartner(models.Model):
    _inherit = "res.partner"

    allowed_category_ids = fields.Many2many(
        comodel_name="product.category",
        relation="tr_sales_rep_access_partner_allowed_category_rel",
        column1="partner_id",
        column2="category_id",
        string="Allowed Product Categories",
        groups=MANAGER_GROUP_XMLID,
        help=(
            "Product categories the rep is allowed to sell from. "
            "Descendants are implicitly included; combined with "
            "excluded_category_ids to compute the effective scope."
        ),
    )
    excluded_category_ids = fields.Many2many(
        comodel_name="product.category",
        relation="tr_sales_rep_access_partner_excluded_category_rel",
        column1="partner_id",
        column2="category_id",
        string="Excluded Product Categories",
        groups=MANAGER_GROUP_XMLID,
        help=(
            "Categories explicitly excluded from the allowed scope, "
            "including their descendants. Use to carve out a subtree "
            "while keeping its parent in allowed."
        ),
    )

    @api.model_create_multi
    def create(self, vals_list):
        default_ids = self._sales_rep_default_catalog_ids()
        if default_ids:
            for vals in vals_list:
                if vals.get("agent") and "allowed_category_ids" not in vals:
                    vals["allowed_category_ids"] = [(6, 0, default_ids)]
        return super().create(vals_list)

    @api.model
    def _sales_rep_default_catalog_ids(self):
        """Return the configured default catalog as a list of IDs.

        Reads from ``ir.config_parameter`` because Many2many cannot use
        the ``config_parameter`` template in ``res.config.settings``.
        Stored as a CSV of integer ids. Ignores IDs whose category no
        longer exists.
        """
        raw = (
            self.env["ir.config_parameter"].sudo().get_param(DEFAULT_CATALOG_PARAM, "")
        )
        if not raw:
            return []
        try:
            ids = [int(x) for x in raw.split(",") if x.strip()]
        except ValueError:
            return []
        Category = self.env["product.category"].sudo()
        return Category.browse(ids).exists().ids

    def _get_visible_category_ids(self):
        """Return the recordset of categories visible to this agent.

        Computed on-the-fly as
        ``descendants(allowed_category_ids) − descendants(
        excluded_category_ids)`` using the native ``child_of``
        operator (indexed via ``parent_path``). Callers use ``.ids``
        on the result to build domains.

        If ``allowed_category_ids`` is empty, returns an empty
        recordset — the fail-safe meaning "sees nothing".
        """
        self.ensure_one()
        Category = self.env["product.category"]
        if not self.allowed_category_ids:
            return Category
        allowed = Category.search([("id", "child_of", self.allowed_category_ids.ids)])
        if not self.excluded_category_ids:
            return allowed
        excluded = Category.search([("id", "child_of", self.excluded_category_ids.ids)])
        return allowed - excluded

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ProductCategory(models.Model):
    _inherit = "product.category"

    tr_exclude_from_general_pricelist = fields.Boolean(
        string="Exclude from general pricelists",
        help=(
            "When checked, products in this category and ALL its "
            "descendants are excluded from the general pricelist "
            "report layouts (By Category and Complete Pricelist). The effect "
            "cascades rigidly — a descendant category cannot override "
            "the exclusion. Products in excluded categories still "
            "appear in the customer history layout, so clients who "
            "already bought them can see the price for reorders."
        ),
    )

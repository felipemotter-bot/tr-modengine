# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ProductCategory(models.Model):
    _inherit = "product.category"

    allow_manual_price_edit = fields.Boolean(
        string="Allow manual price edit on invoices",
        default=False,
        groups="tr_commercial_policy.group_sales_director",
        help="When enabled, users can directly edit price_unit and discount "
        "on invoice lines for products in this category.",
    )

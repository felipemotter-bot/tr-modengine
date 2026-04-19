# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models

from ..hooks import _resolve_group_attribute


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    tr_pricelist_report_category_depth = fields.Integer(
        string="Category Depth",
        config_parameter="tr_pricelist_report.category_depth",
        default=-2,
        help=(
            "Depth used to bucket products by category. -1 = leaf, "
            "-N = Nth ancestor (clamps to root), N >= 0 = absolute "
            "level from root (clamps to leaf). Default -2 (parent of the leaf)."
        ),
    )
    tr_pricelist_report_group_attribute_name = fields.Char(
        string="Group Attribute (Brand Axis)",
        config_parameter="tr_pricelist_report.group_attribute_name",
        default="MARCA",
        help=(
            "Name of the product attribute used as the brand grouping axis "
            "in the General Pricelist layout (Mode A)."
        ),
    )
    tr_pricelist_report_history_months_back = fields.Integer(
        string="Customer History Window (months)",
        config_parameter="tr_pricelist_report.history_months_back",
        default=6,
        help=(
            "Months back the Customer History layout walks over "
            "sale.order.line. Set to 0 to disable the layout."
        ),
    )
    tr_pricelist_report_invalid_price_threshold = fields.Float(
        string="Invalid Price Threshold",
        config_parameter="tr_pricelist_report.invalid_price_threshold",
        default=99999.0,
        help=(
            "Products whose computed price reaches or exceeds this threshold "
            "are dropped from the report. Used to hide 'price not configured' "
            "placeholders (e.g. 999999). Set to 0 to disable the filter."
        ),
    )

    def set_values(self):
        res = super().set_values()
        # Odoo quirk: `0` on Integer/Float ``config_parameter`` fields is
        # collapsed into an empty string by ``set_values`` because
        # ``0 in (None, False)`` evaluates True. All three numeric knobs
        # here accept 0 as a meaningful value (``history_months_back=0``
        # disables the history layout, ``invalid_price_threshold=0``
        # disables the filter, ``category_depth=0`` selects the root
        # category level). Force the literal value so it survives.
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param(
            "tr_pricelist_report.category_depth",
            str(int(self.tr_pricelist_report_category_depth)),
        )
        icp.set_param(
            "tr_pricelist_report.history_months_back",
            str(int(self.tr_pricelist_report_history_months_back)),
        )
        icp.set_param(
            "tr_pricelist_report.invalid_price_threshold",
            str(float(self.tr_pricelist_report_invalid_price_threshold)),
        )
        # Unconditional recompute: the cached group_attribute_id must match
        # whatever name is stored, regardless of whether the user changed it
        # or not this round.
        _resolve_group_attribute(self.env)
        return res

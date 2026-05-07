# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

from ..hooks import _resolve_group_attribute

# Defaults live in code (not in a seed XML) so that ``-u`` never tries to
# INSERT an ``ir.config_parameter`` whose ``key`` already exists in the
# database — that would trip the unique constraint when the param has
# previously been deleted-and-recreated by the core ``set_param`` falsy
# quirk (see comment on ``set_values``).
_DEFAULT_CATEGORY_DEPTH = -2
_DEFAULT_HISTORY_MONTHS_BACK = 6
_DEFAULT_INVALID_PRICE_THRESHOLD = 99999.0
_DEFAULT_GROUP_ATTRIBUTE_NAME = "MARCA"


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # NOTE: these fields intentionally do NOT use ``config_parameter=...``.
    # The framework's auto-binding routes through
    # ``res.config.settings.set_values`` which converts falsy Integer/Float
    # values (0, 0.0) into ``""`` before calling
    # ``ir.config_parameter.set_param``. ``set_param`` then ``unlink()``s
    # the record on falsy values, breaking any seed ``ir.model.data`` that
    # might be linked to it (and forcing the recreation to come back
    # without the xml_id). Values are read/written manually via
    # ``get_values`` / ``set_values`` below.

    tr_pricelist_report_category_depth = fields.Integer(
        string="Category Depth",
        default=_DEFAULT_CATEGORY_DEPTH,
        help=(
            "Depth used to bucket products by category. -1 = leaf, "
            "-N = Nth ancestor (clamps to root), N >= 0 = absolute "
            "level from root (clamps to leaf). Default -2 (parent of the leaf)."
        ),
    )
    tr_pricelist_report_group_attribute_name = fields.Char(
        string="Group Attribute (Brand Axis)",
        default=_DEFAULT_GROUP_ATTRIBUTE_NAME,
        help=(
            "Name of the product attribute used as the brand grouping axis "
            "in the General Pricelist layout (Mode A)."
        ),
    )
    tr_pricelist_report_history_months_back = fields.Integer(
        string="Customer History Window (months)",
        default=_DEFAULT_HISTORY_MONTHS_BACK,
        help=(
            "Months back the Customer History layout walks over "
            "sale.order.line. Set to 0 to disable the layout."
        ),
    )
    tr_pricelist_report_invalid_price_threshold = fields.Float(
        string="Invalid Price Threshold",
        default=_DEFAULT_INVALID_PRICE_THRESHOLD,
        help=(
            "Products whose computed price reaches or exceeds this threshold "
            "are dropped from the report. Used to hide 'price not configured' "
            "placeholders (e.g. 999999). Set to 0 to disable the filter."
        ),
    )

    @api.model
    def _get_int_param(self, key, default):
        """Read an Integer ``ir.config_parameter`` with a typed fallback.

        Returns ``default`` when the param is missing, blank or stores a
        non-integer string. Without the cast guard, garbage in the param
        table (e.g. ``"not_a_number"`` introduced manually or by a
        botched migration) would raise ``ValueError`` from ``int()`` and
        break the Settings page entirely.
        """
        value = self.env["ir.config_parameter"].sudo().get_param(key)
        if value in (None, False, ""):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @api.model
    def _get_float_param(self, key, default):
        """Float counterpart to ``_get_int_param``."""
        value = self.env["ir.config_parameter"].sudo().get_param(key)
        if value in (None, False, ""):
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @api.model
    def get_values(self):
        res = super().get_values()
        icp = self.env["ir.config_parameter"].sudo()
        attribute_name = icp.get_param("tr_pricelist_report.group_attribute_name")
        res.update(
            tr_pricelist_report_category_depth=self._get_int_param(
                "tr_pricelist_report.category_depth", _DEFAULT_CATEGORY_DEPTH
            ),
            tr_pricelist_report_history_months_back=self._get_int_param(
                "tr_pricelist_report.history_months_back",
                _DEFAULT_HISTORY_MONTHS_BACK,
            ),
            tr_pricelist_report_invalid_price_threshold=self._get_float_param(
                "tr_pricelist_report.invalid_price_threshold",
                _DEFAULT_INVALID_PRICE_THRESHOLD,
            ),
            tr_pricelist_report_group_attribute_name=(
                attribute_name or _DEFAULT_GROUP_ATTRIBUTE_NAME
            ),
        )
        return res

    def set_values(self):
        res = super().set_values()
        # All four parameters are written manually (the fields above don't
        # declare ``config_parameter=``) so we control exactly what
        # ``set_param`` sees and never feed it a falsy value that would
        # trigger ``unlink``. Numeric literals are coerced to ``str`` so 0
        # / 0.0 round-trip as ``"0"`` / ``"0.0"`` (meaningful values for
        # all three numeric knobs).
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
        icp.set_param(
            "tr_pricelist_report.group_attribute_name",
            self.tr_pricelist_report_group_attribute_name
            or _DEFAULT_GROUP_ATTRIBUTE_NAME,
        )
        # Unconditional recompute: the cached group_attribute_id must match
        # whatever name is stored, regardless of whether the user changed it
        # or not this round.
        _resolve_group_attribute(self.env)
        return res

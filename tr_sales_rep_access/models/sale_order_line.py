# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

REP_GROUP_XMLID = "tr_sales_rep_access.group_sales_rep_external"
_GROUPS_NO_REP = "!tr_sales_rep_access.group_sales_rep_external"


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    # PR 9 — stock availability fields hidden for reps (field-level RPC block).
    # These are computed by sale_stock and fed to the qty_at_date_widget.
    virtual_available_at_date = fields.Float(groups=_GROUPS_NO_REP)
    qty_available_today = fields.Float(groups=_GROUPS_NO_REP)
    free_qty_today = fields.Float(groups=_GROUPS_NO_REP)
    forecast_expected_date = fields.Datetime(groups=_GROUPS_NO_REP)
    scheduled_date = fields.Datetime(groups=_GROUPS_NO_REP)

    def write(self, vals):
        self._sales_rep_check_rep_can_edit()
        return super().write(vals)

    def unlink(self):
        self._sales_rep_check_rep_can_edit()
        return super().unlink()

    @api.constrains("product_id")
    def _sales_rep_check_product_in_catalog(self):
        """Block rep users from lining products outside their catalog.

        Complements product.[template|product]._search() which filters
        listings: the constraint catches RPC / import / load() paths
        that bypass search by passing product_id directly.
        """
        if not self.env.user.has_group(REP_GROUP_XMLID):
            return
        visible = self.env.user.partner_id.sudo()._get_visible_category_ids()
        if not visible:
            offenders = self.filtered(lambda line: line.product_id)
        else:
            offenders = self.filtered(
                lambda line: line.product_id and line.product_id.categ_id not in visible
            )
        if offenders:
            raise ValidationError(
                _("Product(s) %s are outside your assigned catalog.")
                % ", ".join(offenders.product_id.mapped("display_name"))
            )

    def _sales_rep_check_rep_can_edit(self):
        """Block rep users from touching lines whose order is past draft.

        Uses a Python override (not a state-based record rule) because
        ``sale_stock`` performs internal writes on confirmed order lines
        (propagation of ``product_uom_qty`` to stock moves, etc.) that
        must still work when a non-rep user drives the flow. The rep
        group never executes those code paths.
        """
        if not self.env.user.has_group(REP_GROUP_XMLID):
            return
        non_drafts = self.filtered(lambda line: line.order_id.state != "draft")
        if non_drafts:
            raise AccessError(
                _(
                    "Sales reps can only modify quotation lines in draft. "
                    "Line(s) %s belong to an order past draft."
                )
                % ", ".join(non_drafts.mapped("display_name"))
            )

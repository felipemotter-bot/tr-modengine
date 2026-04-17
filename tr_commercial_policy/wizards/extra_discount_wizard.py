# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import ValidationError


class ExtraDiscountWizard(models.TransientModel):
    _name = "tr.extra.discount.wizard"
    _description = "Extra Discount Wizard"

    sale_line_id = fields.Many2one("sale.order.line", required=True, readonly=True)
    product_name = fields.Char(related="sale_line_id.product_id.display_name")
    current_extra_discount = fields.Float(
        related="sale_line_id.extra_discount", readonly=True
    )
    extra_discount = fields.Float(string="Extra Discount (%)", required=True)
    extra_discount_reason = fields.Char(string="Reason")

    def action_apply(self):
        """Apply extra discount to the sale order line."""
        self.ensure_one()
        if self.extra_discount < 0:
            raise ValidationError(_("Extra discount cannot be negative."))
        if self.extra_discount > 0 and not self.extra_discount_reason:
            raise ValidationError(
                _("A reason is required when setting an extra discount.")
            )
        vals = {
            "extra_discount": self.extra_discount,
            "extra_discount_reason": self.extra_discount_reason or False,
        }
        self.sale_line_id.write(vals)
        return {"type": "ir.actions.act_window_close"}

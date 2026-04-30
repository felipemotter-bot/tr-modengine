# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import AccessError


class SaveConditionLineWizard(models.TransientModel):
    _name = "tr.save.condition.line.wizard"
    _description = "Save Condition Line Wizard"

    sale_line_id = fields.Many2one("sale.order.line", required=True, readonly=True)
    product_id = fields.Many2one("product.product", readonly=True)
    product_tmpl_id = fields.Many2one("product.template", readonly=True)
    seller_discount_current = fields.Float(
        string="Seller Discount (Current)", readonly=True
    )
    seller_discount_new = fields.Float(string="Seller Discount (New)", readonly=True)
    source_level = fields.Char(
        string="Current Source Level",
        readonly=True,
        help="Level of the current condition: general, template or variant.",
    )
    save_as = fields.Selection(
        [("template", "Template"), ("variant", "Variant"), ("none", "Do Not Save")],
        default="template",
    )

    def _check_user_profile(self):
        """Ensure a sales profile can be resolved before saving conditions."""
        if self.env.user.has_group("tr_commercial_policy.group_sales_director"):
            return
        order = self.sale_line_id.order_id
        condition = order.commercial_condition_id
        profile = condition._get_applicable_profile() if condition else False
        if not profile:
            raise AccessError(
                _("No sales profile found for this customer's condition.")
            )

    def action_save(self):
        """Save the line discount to the partner's commercial condition."""
        self.ensure_one()
        if self.save_as == "none":
            return {"type": "ir.actions.act_window_close"}
        self._check_user_profile()

        order = self.sale_line_id.order_id
        condition = order.commercial_condition_id
        if not condition:
            # Wizard is launched from a sale.order line — create the
            # condition in the order's company and pin the partner's
            # company-dependent link in that same scope (multi-company
            # safety; otherwise env.company would drive both, leading
            # to cross-company inconsistencies).
            company = order.company_id or self.env.company
            condition = (
                self.env["partner.commercial.condition"]
                .with_company(company)
                .create(
                    {
                        "partner_id": order.partner_id.id,
                        "company_id": company.id,
                    }
                )
            )
            order.partner_id.with_company(company).commercial_condition_id = condition

        ConditionLine = self.env["partner.commercial.condition.line"]
        if self.save_as == "variant":
            domain = [
                ("condition_id", "=", condition.id),
                ("product_id", "=", self.product_id.id),
            ]
            vals = {
                "applied_on": "product",
                "product_id": self.product_id.id,
                "seller_discount": self.seller_discount_new,
            }
        else:
            domain = [
                ("condition_id", "=", condition.id),
                ("product_tmpl_id", "=", self.product_tmpl_id.id),
                ("product_id", "=", False),
            ]
            vals = {
                "applied_on": "product_template",
                "product_tmpl_id": self.product_tmpl_id.id,
                "seller_discount": self.seller_discount_new,
            }
        existing = ConditionLine.search(domain, limit=1)
        if existing:
            existing.write(vals)
        else:
            vals["condition_id"] = condition.id
            ConditionLine.create(vals)

        return {"type": "ir.actions.act_window_close"}

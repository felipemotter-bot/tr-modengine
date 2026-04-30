# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class SaveConditionWizard(models.TransientModel):
    _name = "tr.save.condition.wizard"
    _description = "Save Condition Wizard"

    order_id = fields.Many2one("sale.order", required=True, readonly=True)
    partner_id = fields.Many2one(related="order_id.partner_id", readonly=True)

    # --- General discounts: current (from condition) vs new (from order) ---
    cash_discount_current = fields.Float(
        string="Cash Discount (Current)", readonly=True
    )
    cash_discount_new = fields.Float(string="Cash Discount (Order)", readonly=True)
    update_cash_discount = fields.Boolean()

    fob_discount_current = fields.Float(string="FOB Discount (Current)", readonly=True)
    fob_discount_new = fields.Float(string="FOB Discount (Order)", readonly=True)
    update_fob_discount = fields.Boolean(string="Update FOB Discount")

    seller_discount_current = fields.Float(
        string="Seller Discount (Current)", readonly=True
    )
    seller_discount_new = fields.Float(string="Seller Discount (Order)", readonly=True)
    update_seller_discount = fields.Boolean()

    line_ids = fields.One2many(
        "tr.save.condition.wizard.line",
        "wizard_id",
        string="Product Lines",
    )
    all_same_discount = fields.Boolean(
        compute="_compute_all_same_discount",
        string="All Lines Same Discount",
    )

    @api.depends("line_ids.seller_discount_new")
    def _compute_all_same_discount(self):
        for wizard in self:
            lines = wizard.line_ids.filtered(lambda line: not line.ignore)
            if not lines:
                wizard.all_same_discount = True
                continue
            first = lines[0].seller_discount_new
            wizard.all_same_discount = all(
                line.seller_discount_new == first for line in lines
            )

    def _check_user_profile(self):
        """Ensure a sales profile can be resolved before saving conditions."""
        if self.env.user.has_group("tr_commercial_policy.group_sales_director"):
            return
        condition = self.order_id.commercial_condition_id
        profile = condition._get_applicable_profile() if condition else False
        if not profile:
            raise AccessError(
                _("No sales profile found for this customer's condition.")
            )

    def action_save(self):
        """Save selected discounts to the partner's commercial condition."""
        self.ensure_one()
        self._check_user_profile()

        condition = self.order_id.commercial_condition_id
        if not condition:
            # Wizard is launched from a sale.order — create the condition
            # in the order's company so the partner's company-dependent
            # ``commercial_condition_id`` and the new condition's
            # ``company_id`` line up. Without ``with_company`` here, the
            # condition is created against ``env.company`` (which may be
            # different from the order's company in multi-company setups)
            # and the partner write below would store the link under the
            # wrong company.
            company = self.order_id.company_id or self.env.company
            condition = (
                self.env["partner.commercial.condition"]
                .with_company(company)
                .create(
                    {
                        "partner_id": self.partner_id.id,
                        "company_id": company.id,
                    }
                )
            )
            self.partner_id.with_company(company).commercial_condition_id = condition

        # Update general discounts (validation delegated to condition.write)
        vals = {}
        if self.update_cash_discount:
            vals["cash_discount"] = self.cash_discount_new
        if self.update_fob_discount:
            vals["fob_discount"] = self.fob_discount_new
        if self.update_seller_discount:
            vals["seller_discount"] = self.seller_discount_new
        if vals:
            condition.write(vals)

        # Update product-specific lines
        for wiz_line in self.line_ids.filtered(lambda line: not line.ignore):
            wiz_line._save_to_condition(condition)

        return {"type": "ir.actions.act_window_close"}


class SaveConditionWizardLine(models.TransientModel):
    _name = "tr.save.condition.wizard.line"
    _description = "Save Condition Wizard Line"

    wizard_id = fields.Many2one(
        "tr.save.condition.wizard", required=True, ondelete="cascade"
    )
    sale_line_id = fields.Many2one("sale.order.line", readonly=True)
    product_id = fields.Many2one("product.product", readonly=True)
    product_tmpl_id = fields.Many2one("product.template", readonly=True)
    seller_discount_current = fields.Float(
        string="Seller Discount (Current)", readonly=True
    )
    seller_discount_new = fields.Float(string="Seller Discount (Order)", readonly=True)
    extra_discount_current = fields.Float(
        string="Extra Discount (Current)", readonly=True
    )
    extra_discount_new = fields.Float(string="Extra Discount (Order)", readonly=True)
    save_as = fields.Selection(
        [("template", "Template"), ("variant", "Variant")],
        default="template",
    )
    ignore = fields.Boolean()

    def _save_to_condition(self, condition):
        """Create or update a condition line based on save_as selection."""
        self.ensure_one()
        if self.save_as == "variant":
            domain = [
                ("condition_id", "=", condition.id),
                ("product_id", "=", self.product_id.id),
            ]
            vals = {
                "applied_on": "product",
                "product_id": self.product_id.id,
                "seller_discount": self.seller_discount_new,
                "extra_discount": self.extra_discount_new,
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
                "extra_discount": self.extra_discount_new,
            }
        existing = self.env["partner.commercial.condition.line"].search(domain, limit=1)
        if existing:
            existing.write(vals)
        else:
            vals["condition_id"] = condition.id
            self.env["partner.commercial.condition.line"].create(vals)

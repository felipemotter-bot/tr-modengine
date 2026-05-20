from odoo import fields, models


class ProductPricelist(models.Model):
    _name = "product.pricelist"
    _inherit = ["product.pricelist", "mail.thread", "mail.activity.mixin"]

    name = fields.Char(tracking=True)
    currency_id = fields.Many2one(tracking=True)
    company_id = fields.Many2one(tracking=True)
    active = fields.Boolean(tracking=True)
    discount_policy = fields.Selection(tracking=True)

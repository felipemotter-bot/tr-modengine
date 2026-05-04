# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.misc import formatLang


class SalesProfile(models.Model):
    _name = "tr.sales.profile"
    _description = "Sales Profile"
    _check_company_auto = True

    name = fields.Char(required=True)
    profile_type = fields.Selection(
        selection=[
            ("agent", "Agent"),
            ("internal", "Internal"),
        ],
        required=True,
    )
    active = fields.Boolean(default=True)
    cash_discount_max = fields.Float(string="Max Cash Discount (%)")
    fob_discount_max = fields.Float(string="Max FOB Discount (%)")
    cash_term_avg_days_max = fields.Integer(
        string="Max Avg Days for Cash Discount",
        help="Maximum average payment term days to allow cash discount.",
    )
    payment_mode_ids = fields.Many2many(
        comodel_name="account.payment.mode",
        string="Allowed Payment Modes",
        check_company=True,
        domain="[('payment_type', '=', 'inbound'), ('company_id', '=', company_id)]",
    )
    pricelist_ids = fields.Many2many(
        comodel_name="product.pricelist",
        string="Allowed Pricelists",
        domain="[('company_id', 'in', (company_id, False))]",
        help="Pricelists allowed for this profile. "
        "If empty, any pricelist is accepted.",
    )
    manager_extra_limit = fields.Float(
        string="Manager Extra Limit (%)",
        help="Extra discount up to this limit can be approved by manager. "
        "Above this requires director approval.",
    )
    rule_ids = fields.One2many(
        comodel_name="tr.sales.profile.rule",
        inverse_name="profile_id",
        string="Rules",
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        required=True,
        default=lambda self: self.env.company,
    )

    @api.model
    def _get_my_profile_ids(self):
        partner = self.env.user.partner_id
        ids = []
        for company in self.env.companies:
            profile = partner.with_company(company).sales_profile_id
            if profile:
                ids.append(profile.id)
        return ids

    @api.model
    def action_my_profiles(self):
        ids = self._get_my_profile_ids()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "tr_commercial_policy.tr_sales_profile_my_action"
        )
        action["domain"] = [("id", "in", ids)]
        return action

    @api.constrains("rule_ids", "profile_type")
    def _check_at_least_one_rule(self):
        for profile in self:
            if not profile.rule_ids:
                raise ValidationError(
                    _(
                        "Profile '%s' must have at least one rule. "
                        "Without a rule, lines using this profile end up "
                        "with commissions not managed by the commercial "
                        "policy.",
                        profile.name,
                    )
                )


class SalesProfileRule(models.Model):
    _name = "tr.sales.profile.rule"
    _description = "Sales Profile Rule"
    _order = "applied_on, sequence"

    profile_id = fields.Many2one(
        comodel_name="tr.sales.profile",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    applied_on = fields.Selection(
        selection=[
            ("product", "Product Variant"),
            ("product_template", "Product Template"),
            ("category", "Product Category"),
            ("general", "General"),
        ],
        required=True,
        default="general",
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="Product Variant",
        domain="[('sale_ok', '=', True)]",
    )
    product_tmpl_id = fields.Many2one(
        comodel_name="product.template",
        string="Product Template",
        domain="[('sale_ok', '=', True)]",
    )
    categ_id = fields.Many2one(
        comodel_name="product.category",
        string="Product Category",
    )
    qty_min = fields.Float(
        string="Min Qty",
        default=0.0,
        help=(
            "Minimum quantity on the order/invoice line for this rule to be "
            "applicable. When 0, the rule applies regardless of quantity. "
            "Multiple rules in the same scope (e.g. same product variant) with "
            "increasing qty_min values define volume bands — the highest band "
            "whose threshold is met wins.\n\n"
            "Fallback: when the most specific rule does not meet qty_min, "
            "resolution moves to the next level (variant → template → "
            "category → general). This is by design, not a bug."
        ),
    )
    qty_uom_id = fields.Many2one(
        comodel_name="uom.uom",
        string="Qty UoM",
        help=(
            "Unit of measure used to compare the line quantity with qty_min. "
            "Required when qty_min > 0. The line quantity is converted to "
            "this UoM before comparison. If conversion is not possible "
            "(different UoM categories), the rule is ignored and resolution "
            "falls back to the next level."
        ),
    )
    seller_discount_max = fields.Float(
        string="Max Seller Discount (%)",
        compute="_compute_seller_discount_max",
        store=True,
        help="Automatically set from the highest band value "
        "(commission bands for agents, order value bands for internals).",
    )
    commission_band_ids = fields.One2many(
        comodel_name="tr.sales.profile.commission.band",
        inverse_name="rule_id",
        string="Commission Bands",
    )
    order_value_band_ids = fields.One2many(
        comodel_name="tr.sales.profile.order.band",
        inverse_name="rule_id",
        string="Order Value Bands",
    )
    bands_summary = fields.Text(
        string="Bands",
        compute="_compute_bands_summary",
    )

    @api.depends(
        "commission_band_ids",
        "commission_band_ids.discount_up_to",
        "commission_band_ids.commission_rate",
        "order_value_band_ids",
        "order_value_band_ids.order_min_amount",
        "order_value_band_ids.seller_discount_max",
        "profile_id.profile_type",
    )
    def _compute_bands_summary(self):
        def fmt(val):
            return formatLang(self.env, val, digits=2)

        for rule in self:
            profile_type = rule.profile_id.profile_type
            if profile_type == "agent" and rule.commission_band_ids:
                bands = rule.commission_band_ids.sorted("discount_up_to")
                parts = []
                prev = 0.0
                for band in bands:
                    parts.append(
                        _(
                            "Disc. %(start)s-%(end)s%% → Comm. %(rate)s%%",
                            start=fmt(prev),
                            end=fmt(band.discount_up_to),
                            rate=fmt(band.commission_rate),
                        )
                    )
                    prev = band.discount_up_to
                rule.bands_summary = "\n".join(parts)
            elif profile_type == "internal" and rule.order_value_band_ids:
                bands = rule.order_value_band_ids.sorted("order_min_amount")
                parts = []
                for band in bands:
                    fmt_amount = formatLang(
                        self.env,
                        band.order_min_amount,
                        monetary=True,
                        currency_obj=self.env.company.currency_id,
                    )
                    fmt_disc = fmt(band.seller_discount_max)
                    parts.append(
                        _(
                            "Min. %(amount)s → Disc. max %(disc)s%%",
                            amount=fmt_amount,
                            disc=fmt_disc,
                        )
                    )
                rule.bands_summary = "\n".join(parts)
            else:
                rule.bands_summary = False

    @api.depends(
        "commission_band_ids",
        "commission_band_ids.discount_up_to",
        "order_value_band_ids",
        "order_value_band_ids.seller_discount_max",
        "profile_id.profile_type",
    )
    def _compute_seller_discount_max(self):
        for rule in self:
            if rule.profile_id.profile_type == "agent" and rule.commission_band_ids:
                last_band = rule.commission_band_ids.sorted("discount_up_to")[-1]
                rule.seller_discount_max = last_band.discount_up_to
            elif (
                rule.profile_id.profile_type == "internal" and rule.order_value_band_ids
            ):
                last_band = rule.order_value_band_ids.sorted("order_min_amount")[-1]
                rule.seller_discount_max = last_band.seller_discount_max
            else:
                rule.seller_discount_max = 0.0

    @api.constrains("applied_on", "product_id", "product_tmpl_id", "categ_id")
    def _check_applied_on_fields(self):
        for rule in self:
            if rule.applied_on == "product" and not rule.product_id:
                raise ValidationError(
                    _("Product variant is required when applied on 'Product Variant'.")
                )
            if rule.applied_on == "product_template" and not rule.product_tmpl_id:
                raise ValidationError(
                    _(
                        "Product template is required when applied on"
                        " 'Product Template'."
                    )
                )
            if rule.applied_on == "category" and not rule.categ_id:
                raise ValidationError(
                    _(
                        "Product category is required when applied on"
                        " 'Product Category'."
                    )
                )

    @api.constrains("qty_min", "qty_uom_id")
    def _check_qty_min(self):
        for rule in self:
            if rule.qty_min < 0:
                raise ValidationError(
                    _("Min Qty cannot be negative (got %.4f).") % rule.qty_min
                )
            if rule.qty_min > 0 and not rule.qty_uom_id:
                raise ValidationError(
                    _(
                        "Min Qty is set (%.4f) but Qty UoM is missing. "
                        "Define a unit of measure so the line quantity can "
                        "be compared."
                    )
                    % rule.qty_min
                )

    @api.onchange("qty_min", "applied_on", "product_id", "product_tmpl_id")
    def _onchange_qty_min(self):
        if self.qty_min and not self.qty_uom_id:
            if self.applied_on == "product" and self.product_id:
                self.qty_uom_id = self.product_id.uom_id
            elif self.applied_on == "product_template" and self.product_tmpl_id:
                self.qty_uom_id = self.product_tmpl_id.uom_id
            else:
                return {
                    "warning": {
                        "title": _("Qty UoM Required"),
                        "message": _(
                            "Define the UoM for Min Qty. The unit could "
                            "not be inferred automatically for this rule "
                            "scope."
                        ),
                    }
                }

    @api.constrains("profile_id", "commission_band_ids", "order_value_band_ids")
    def _check_bands_required(self):
        for rule in self:
            profile_type = rule.profile_id.profile_type
            if profile_type == "agent" and not rule.commission_band_ids:
                raise ValidationError(
                    _(
                        "Agent profiles require at least one commission band "
                        "per rule."
                    )
                )
            if profile_type == "internal" and not rule.order_value_band_ids:
                raise ValidationError(
                    _(
                        "Internal profiles require at least one order value "
                        "band per rule."
                    )
                )


class SalesProfileCommissionBand(models.Model):
    _name = "tr.sales.profile.commission.band"
    _description = "Commission Band"
    _order = "discount_up_to"

    rule_id = fields.Many2one(
        comodel_name="tr.sales.profile.rule",
        required=True,
        ondelete="cascade",
    )
    discount_up_to = fields.Float(
        string="Discount Up To (%)",
        required=True,
    )
    commission_rate = fields.Float(
        string="Commission Rate (%)",
        required=True,
    )

    @api.constrains("commission_rate")
    def _check_commission_rate_non_negative(self):
        for record in self:
            if record.commission_rate < 0:
                raise ValidationError(
                    _("Commission rate cannot be negative (got %.2f%%).")
                    % record.commission_rate
                )

    @api.constrains("discount_up_to", "commission_rate")
    def _check_strictly_decreasing(self):
        for band in self:
            bands = band.rule_id.commission_band_ids.sorted("discount_up_to")
            prev_rate = None
            for rec in bands:
                if prev_rate is not None and rec.commission_rate >= prev_rate:
                    raise ValidationError(  # noqa: UP031
                        _(
                            "Commission rate must be strictly decreasing as "
                            "discount increases. Band '%(discount).2f%%' has rate "
                            "'%(rate).2f%%' which is not less than the previous rate."
                        )
                        % {"discount": rec.discount_up_to, "rate": rec.commission_rate}
                    )
                prev_rate = rec.commission_rate


class SalesProfileOrderBand(models.Model):
    _name = "tr.sales.profile.order.band"
    _description = "Order Value Band"
    _order = "order_min_amount"

    rule_id = fields.Many2one(
        comodel_name="tr.sales.profile.rule",
        required=True,
        ondelete="cascade",
    )
    order_min_amount = fields.Float(
        string="Minimum Order Amount",
        required=True,
    )
    seller_discount_max = fields.Float(
        string="Max Seller Discount (%)",
        required=True,
    )

    @api.constrains("order_min_amount", "seller_discount_max")
    def _check_strictly_increasing(self):
        for band in self:
            bands = band.rule_id.order_value_band_ids.sorted("order_min_amount")
            prev_discount = None
            for rec in bands:
                if (
                    prev_discount is not None
                    and rec.seller_discount_max <= prev_discount
                ):
                    raise ValidationError(  # noqa: UP031
                        _(
                            "Max seller discount must be strictly increasing as "
                            "order amount increases. Band 'R$ %(amount).2f' has "
                            "discount '%(disc).2f%%' which is not greater than "
                            "the previous discount."
                        )
                        % {
                            "amount": rec.order_min_amount,
                            "disc": rec.seller_discount_max,
                        }
                    )
                prev_discount = rec.seller_discount_max

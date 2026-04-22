# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .policy_utils import (
    calc_adjustment_factor,
    calc_price_unit,
    calc_reference_price,
    get_policy_rates,
    get_seller_markup_max_pct,
    resolve_applicable_rule,
    validate_seller_markup,
)


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    price_unit = fields.Float(digits="Sale Price")
    base_price = fields.Float(
        digits="Sale Price",
        compute="_compute_base_price",
        store=True,
        readonly=False,
        help="Price from the pricelist (base table price).",
    )
    reference_price = fields.Float(
        digits="Sale Price",
        compute="_compute_reference_price",
        store=True,
        help="Base price adjusted by contractual return.",
    )
    seller_discount = fields.Float(
        string="Seller Discount (%)",
        digits="Discount Policy",
    )
    extra_discount = fields.Float(
        string="Extra Discount (%)",
        digits="Discount Policy",
    )
    extra_discount_reason = fields.Char()
    total_seller_extra_discount = fields.Float(
        string="Total Discount (%)",
        compute="_compute_total_seller_extra_discount",
        digits="Discount Policy",
        help="Sum of seller discount and extra discount.",
    )
    profile_type = fields.Selection(
        related="order_id.sales_profile_id.profile_type",
    )
    contractual_return = fields.Float(
        related="order_id.contractual_return",
        string="Contractual Return (%)",
    )
    adjustment_factor = fields.Float(
        string="Adjustment Factor (%)",
        compute="_compute_reference_price",
        store=True,
        help="Price adjustment percentage due to contractual return.",
    )
    seller_discount_max = fields.Float(
        string="Max Seller Discount (%)",
        compute="_compute_seller_discount_max",
        digits="Discount Policy",
        help="Maximum seller discount allowed by the sales profile for this product.",
    )
    seller_markup_max = fields.Float(
        string="Max Seller Markup (%)",
        compute="_compute_seller_markup_max",
        digits="Discount Policy",
        help="Maximum markup (negative seller discount) allowed globally.",
    )
    discount_changed = fields.Boolean(
        compute="_compute_discount_changed",
        help="True when seller/extra discount differs from the commercial condition.",
    )

    @api.depends(
        "product_id",
        "product_uom",
        "product_uom_qty",
        "order_id.pricelist_id",
        "order_id.partner_id",
    )
    def _compute_base_price(self):
        force = self.env.context.get("force_policy_recompute")
        for line in self:
            if not force and line.order_id.state not in ("draft", "sent"):
                continue
            if not line.product_id or not line.order_id.pricelist_id:
                line.base_price = 0.0
                continue
            price = line.with_company(line.company_id)._get_display_price()
            line.base_price = line.product_id._get_tax_included_unit_price(
                line.company_id,
                line.order_id.currency_id,
                line.order_id.date_order,
                "sale",
                fiscal_position=line.order_id.fiscal_position_id,
                product_price_unit=price,
                product_currency=line.currency_id,
            )

    @api.depends("base_price", "order_id.contractual_return")
    def _compute_reference_price(self):
        force = self.env.context.get("force_policy_recompute")
        tax_rate, freight_rate, admin_rate = get_policy_rates(self.env)
        for line in self:
            if not force and line.order_id.state not in ("draft", "sent"):
                continue
            cr = line.order_id.contractual_return
            line.adjustment_factor = calc_adjustment_factor(
                cr, tax_rate, freight_rate, admin_rate
            )
            line.reference_price = calc_reference_price(
                line.base_price, cr, tax_rate, freight_rate, admin_rate
            )

    # Override WITHOUT @api.depends to preserve precompute on price_unit.
    # Standard depends ('product_id', 'product_uom', 'product_uom_qty') are
    # inherited from the base class.  Reactivity to seller_discount /
    # extra_discount is handled via onchange + _apply_condition_to_line.
    def _compute_price_unit(self):
        force = self.env.context.get("force_policy_recompute")
        for line in self:
            if not force and line.order_id.state not in ("draft", "sent"):
                continue
            if not line.product_id:
                line.price_unit = 0.0
                continue
            if line.qty_invoiced > 0:
                continue
            if not line.reference_price:
                line.price_unit = 0.0
                continue
            line.price_unit = calc_price_unit(
                line.reference_price, line.seller_discount, line.extra_discount
            )
        return True

    # Override WITHOUT @api.depends to preserve precompute on discount.
    # Standard depends are inherited from l10n_br_sale.
    # Reactivity to cash/fob is handled via onchange on sale.order.
    def _compute_discounts(self):
        result = super()._compute_discounts()
        for line in self:
            if line.order_id.state not in ("draft", "sent"):
                continue
            line._ensure_policy_discount()
        return result

    def _ensure_policy_discount(self):
        """Fallback: ensure line discount reflects the commercial policy.

        Only acts when a commercial condition is active on the order.
        Corrects divergences caused by l10n_br_sale flags
        (user_total_discount, discount_fixed) that may prevent super()
        from propagating discount_rate correctly.
        """
        if not self.order_id.commercial_condition_id:
            return
        expected = (self.order_id.cash_discount or 0) + (
            self.order_id.fob_discount or 0
        )
        if self.discount != expected:
            self.discount = expected
            self.discount_value = (
                (self.product_uom_qty * self.price_unit) * expected / 100
            )

    commission_rate = fields.Float(
        string="Commission (%)",
        compute="_compute_commission_rate",
        help="Dynamic commission rate based on seller discount and commission bands.",
    )

    @api.depends(
        "product_id",
        "order_id.sales_profile_id",
        "order_id.sales_profile_id.rule_ids",
        "order_id.amount_untaxed",
    )
    def _compute_seller_discount_max(self):
        for line in self:
            line.seller_discount_max = line._get_seller_discount_max()

    def _compute_seller_markup_max(self):
        markup_max = get_seller_markup_max_pct(self.env)
        for line in self:
            line.seller_markup_max = markup_max

    @api.depends(
        "seller_discount",
        "product_id",
        "order_id.commercial_condition_id",
        "order_id.commercial_condition_id.seller_discount",
        "order_id.commercial_condition_id.line_ids.seller_discount",
    )
    def _compute_discount_changed(self):
        for line in self:
            if not line.product_id or not line.order_id.commercial_condition_id:
                line.discount_changed = False
                continue
            (
                cond_seller,
                _cond_extra,
                _source,
            ) = line.order_id._get_condition_discount_for_product(line.product_id)
            line.discount_changed = line.seller_discount != cond_seller

    @api.depends("seller_discount", "extra_discount")
    def _compute_total_seller_extra_discount(self):
        for line in self:
            line.total_seller_extra_discount = (line.seller_discount or 0) + (
                line.extra_discount or 0
            )

    @api.depends(
        "seller_discount",
        "product_id",
        "order_id.sales_profile_id",
        "order_id.sales_profile_id.rule_ids",
        "order_id.sales_profile_id.rule_ids.commission_band_ids",
    )
    def _compute_commission_rate(self):
        for line in self:
            line.commission_rate = line._get_commission_rate_from_bands()

    @api.constrains("seller_discount")
    def _check_seller_discount_limit(self):
        self._validate_seller_discount_limit()

    def _validate_seller_discount_limit(self):
        """Check seller discount against the absolute max for each line.

        Uses ``_get_seller_discount_absolute_max`` (highest band limit)
        so that band-contextual checks are handled separately by
        ``_get_discount_validation_issues`` (tier validation).
        Also enforces the global markup limit for negative discounts.
        """
        for line in self:
            validate_seller_markup(self.env, line.seller_discount)
            max_disc = line._get_seller_discount_absolute_max()
            if line.seller_discount > max_disc:
                raise ValidationError(  # noqa: UP031
                    _(
                        "Seller discount (%(disc).2f%%) exceeds the"
                        " maximum allowed (%(max).2f%%) for"
                        " product '%(product)s'."
                    )
                    % {
                        "disc": line.seller_discount,
                        "max": max_disc,
                        "product": line.product_id.display_name or "",
                    }
                )

    @api.constrains("extra_discount", "seller_discount")
    def _check_discount_range(self):
        for line in self:
            if (line.seller_discount or 0) < 0 and (line.extra_discount or 0) > 0:
                raise ValidationError(
                    _("Extra discount cannot be combined with a seller markup.")
                )
            if line.extra_discount < 0:
                raise ValidationError(_("Extra discount cannot be negative."))
            if line.extra_discount > 99:
                raise ValidationError(_("Extra discount cannot exceed 99%%."))
            total = (line.seller_discount or 0) + (line.extra_discount or 0)
            if total > 99:
                raise ValidationError(
                    _(
                        "Total discount (seller %(seller).2f%% + extra"
                        " %(extra).2f%% = %(total).2f%%) cannot exceed 99%%."
                    )
                    % {
                        "seller": line.seller_discount,
                        "extra": line.extra_discount,
                        "total": total,
                    }
                )

    @api.onchange("product_id")
    def _onchange_product_id_apply_condition(self):
        if not self.product_id:
            return
        if not self.order_id.sales_profile_id:
            return {
                "warning": {
                    "title": _("No Sales Profile"),
                    "message": _(
                        "This order has no sales profile. "
                        "Pricing may be incorrect and confirmation "
                        "will be blocked."
                    ),
                }
            }
        if self.order_id.commercial_condition_id:
            self.order_id._apply_condition_to_line(
                self, self.order_id.commercial_condition_id
            )

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._resolve_agent_commissions()
        return lines

    def write(self, vals):
        self._check_direct_price_edit(vals)
        res = super().write(vals)
        if any(
            field in vals
            for field in ("seller_discount", "extra_discount", "reference_price")
        ):
            self._recompute_price_unit_from_policy()
        if any(field in vals for field in ("seller_discount",)):
            self._resolve_agent_commissions()
        if any(field in vals for field in ("extra_discount", "seller_discount")):
            orders_to_revalidate = self.mapped("order_id").filtered("review_ids")
            for order in orders_to_revalidate:
                order._sync_discount_validation_state()
        return res

    def button_edit_agents(self):
        """Open agent editor — readonly for non-directors."""
        action = super().button_edit_agents()
        if not self.env.user.has_group("tr_commercial_policy.group_sales_director"):
            action["flags"] = {"mode": "readonly"}
        return action

    def _resolve_agent_commissions(self):
        """Resolve managed commission on agent lines after line create/write.

        For agent profiles with an applicable band, ensures each agent line
        points to the managed commission matching the agent modality
        (invoice_state) and the effective band rate. A rate of 0% creates
        a managed commission that produces zero commission amount.

        Uses _get_commission_rate_from_bands() directly to distinguish
        "band resolves 0%" (returns 0.0) from "no band matched" (returns
        False).
        """
        Commission = self.env["commission"]
        for line in self:
            profile = line.order_id.sales_profile_id
            if not profile or profile.profile_type != "agent":
                continue
            band_rate = line._get_commission_rate_from_bands()
            if band_rate is False:
                continue
            for agent_line in line.agent_ids:
                base_commission = agent_line.agent_id.commission_id
                commission = Commission._ensure_managed_commission(
                    base_commission.invoice_state, band_rate
                )
                if commission != agent_line.commission_id:
                    agent_line.commission_id = commission

    def _check_direct_price_edit(self, vals):
        """Block direct writes to price_unit/discount.

        Price and discount are always computed — direct editing is prohibited
        (policy 4.1). Allows writes from compute methods (context flag) and
        system admins.
        """
        protected_fields = {"price_unit", "discount"}
        if not protected_fields.intersection(vals):
            return
        if self.env.context.get("tr_skip_price_protection"):
            return
        if self.env.user.has_group("base.group_system"):
            return
        raise ValidationError(
            _(
                "Direct editing of price and discount is not allowed. "
                "Use seller discount and extra discount fields instead."
            )
        )

    def _recompute_price_unit_from_policy(self):
        """Recalculate price_unit based on policy values."""
        for line in self:
            if not line.reference_price:
                continue
            if line.qty_invoiced > 0:
                continue
            new_price = calc_price_unit(
                line.reference_price, line.seller_discount, line.extra_discount
            )
            line.with_context(tr_skip_price_protection=True).price_unit = new_price

    def _prepare_agent_vals(self, agent):
        """Pre-fill an existing managed commission on agent line creation.

        Only reuses an already existing managed commission to avoid creating
        records during _compute_agent_ids. Missing records are created later
        by _resolve_agent_commissions after the line exists.
        """
        result = super()._prepare_agent_vals(agent)
        if not isinstance(self.id, int):
            return result
        profile = self.order_id.sales_profile_id
        if profile and profile.profile_type == "agent":
            band_rate = self._get_commission_rate_from_bands()
            if band_rate is not False:
                base_commission = agent.commission_id
                if base_commission:
                    commission = self.env["commission"]._find_managed_commission(
                        base_commission.invoice_state, band_rate
                    )
                    if commission:
                        result["commission_id"] = commission.id
        return result

    def _prepare_invoice_line(self, **optional_values):
        """Propagate commercial policy fields to the invoice line."""
        vals = super()._prepare_invoice_line(**optional_values)
        vals.update(
            {
                "seller_discount": self.seller_discount,
                "extra_discount": self.extra_discount,
                "extra_discount_reason": self.extra_discount_reason or False,
                "base_price": self.base_price,
                "reference_price": self.reference_price,
                "commission_rate": self.commission_rate,
            }
        )
        return vals

    def action_open_save_condition_line_wizard(self):
        """Open the wizard to save this line's discount to the customer condition."""
        self.ensure_one()
        order = self.order_id
        _seller, _extra, source = order._get_condition_discount_for_product(
            self.product_id
        )
        default_save_as = "variant" if source == "variant" else "template"
        wizard = self.env["tr.save.condition.line.wizard"].create(
            {
                "sale_line_id": self.id,
                "product_id": self.product_id.id,
                "product_tmpl_id": self.product_id.product_tmpl_id.id,
                "seller_discount_current": _seller,
                "seller_discount_new": self.seller_discount,
                "source_level": source,
                "save_as": default_save_as,
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "tr.save.condition.line.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
            "name": "Save Line Condition",
        }

    def action_open_extra_discount_wizard(self):
        """Open wizard to set extra discount on this line."""
        self.ensure_one()
        wizard = self.env["tr.extra.discount.wizard"].create(
            {
                "sale_line_id": self.id,
                "extra_discount": self.extra_discount,
                "extra_discount_reason": self.extra_discount_reason or "",
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "tr.extra.discount.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
            "name": _("Extra Discount"),
        }

    def action_clear_extra_discount(self):
        """Remove extra discount from this line."""
        self.ensure_one()
        self.write(
            {
                "extra_discount": 0.0,
                "extra_discount_reason": False,
            }
        )

    @api.onchange("seller_discount", "extra_discount")
    def _onchange_seller_extra_discount(self):
        """Recalculate price_unit when seller or extra discount changes in UI."""
        if self.order_id.sales_profile_id and self.reference_price:
            self.price_unit = calc_price_unit(
                self.reference_price, self.seller_discount, self.extra_discount
            )

    def _get_applicable_rule(self):
        """Resolve the most specific profile rule for this line's product.

        Resolution order: product variant > product template > category > general.
        Returns the matching ``tr.sales.profile.rule`` record or empty recordset.
        """
        self.ensure_one()
        profile = self.order_id.sales_profile_id
        if not profile or not self.product_id:
            return self.env["tr.sales.profile.rule"]
        return resolve_applicable_rule(
            self.product_id, profile.rule_ids, self.env["tr.sales.profile.rule"]
        )

    def _get_seller_discount_max(self):
        """Resolve max seller discount from the applicable profile rule.

        For internal profiles with order value bands, returns the max
        from the band matching the current order ``amount_untaxed``.
        For agent profiles, returns the static max from the rule.
        """
        self.ensure_one()
        rule = self._get_applicable_rule()
        if not rule:
            return 0.0
        profile = self.order_id.sales_profile_id
        if profile.profile_type == "internal" and rule.order_value_band_ids:
            order_amount = self.order_id.amount_untaxed or 0.0
            applicable = rule.order_value_band_ids.filtered(
                lambda band, amt=order_amount: band.order_min_amount <= amt
            ).sorted("order_min_amount")
            return applicable[-1].seller_discount_max if applicable else 0.0
        return rule.seller_discount_max

    def _get_seller_discount_absolute_max(self):
        """Return the absolute maximum seller discount for the profile rule.

        Unlike ``_get_seller_discount_max`` (which is contextual to the
        current order amount for internal bands), this returns the highest
        possible limit defined in the rule — i.e. the highest band's max.
        Used by ``_validate_seller_discount_limit`` so that band-contextual
        checks are left to ``_get_discount_validation_issues``.
        """
        self.ensure_one()
        rule = self._get_applicable_rule()
        if not rule:
            return 0.0
        if rule.order_value_band_ids:
            return max(rule.order_value_band_ids.mapped("seller_discount_max"))
        return rule.seller_discount_max

    def _get_commission_rate_from_bands(self):
        """Resolve commission rate from bands based on seller_discount.

        Wrapper around ``_get_commission_rate_for_discount`` using the
        line's current ``seller_discount``.
        """
        self.ensure_one()
        return self._get_commission_rate_for_discount(self.seller_discount)

    def _get_commission_rate_for_discount(self, seller_discount):
        """Resolve commission rate from bands for a given discount.

        Finds the first band where ``discount_up_to >= seller_discount``
        and returns its ``commission_rate``.  Returns ``False`` when no
        applicable band matches (different from an explicit 0.0 rate).
        """
        self.ensure_one()
        rule = self._get_applicable_rule()
        if not rule or not rule.commission_band_ids:
            return False
        seller_disc = max(seller_discount or 0.0, 0.0)
        bands = rule.commission_band_ids.sorted("discount_up_to")
        for band in bands:
            if band.discount_up_to >= seller_disc:
                return band.commission_rate
        return False

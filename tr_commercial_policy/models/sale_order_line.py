# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare

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
    locked_line_id = fields.Many2one(
        comodel_name="partner.commercial.condition.locked.line",
        string="Locked Line",
        readonly=True,
        index=True,
        ondelete="restrict",
        help="Director-defined locked line that resolved for this product. "
        "When set, the discount and commission were decided by the sales "
        "director, not by the rep. Edits to seller/extra discount are "
        "blocked for the rep; manager and director can apply pontual "
        "overrides. The locked line itself is edited only on the "
        "commercial condition record.",
    )
    locked_fixed_commission_rate = fields.Float(
        string="Locked Fixed Commission (%)",
        readonly=True,
        digits="Discount Policy",
        help="Snapshot of the locked line's fixed_commission_rate at the "
        "moment the condition was applied. Used as the single source of "
        "truth for commission on locked lines so later edits to the "
        "locked line do not affect existing orders/invoices.",
    )
    locked_baseline_seller_discount = fields.Float(
        string="Locked Baseline Seller Discount (%)",
        readonly=True,
        digits="Discount Policy",
        help="Snapshot of the seller discount produced by the locked "
        "line at apply time. Used by tier validation to detect manual "
        "overrides — comparison stays stable across later edits of "
        "the locked record itself.",
    )
    locked_baseline_extra_discount = fields.Float(
        string="Locked Baseline Extra Discount (%)",
        readonly=True,
        digits="Discount Policy",
        help="Snapshot of the extra discount produced by the locked "
        "line at apply time. Pair with locked_baseline_seller_discount.",
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
        "product_uom",
        "product_uom_qty",
        "order_id.sales_profile_id",
        "order_id.sales_profile_id.rule_ids",
        "order_id.sales_profile_id.rule_ids.applied_on",
        "order_id.sales_profile_id.rule_ids.product_id",
        "order_id.sales_profile_id.rule_ids.product_tmpl_id",
        "order_id.sales_profile_id.rule_ids.categ_id",
        "order_id.sales_profile_id.rule_ids.qty_min",
        "order_id.sales_profile_id.rule_ids.qty_uom_id",
        "order_id.sales_profile_id.rule_ids.sequence",
        "order_id.amount_untaxed",
    )
    def _compute_seller_discount_max(self):
        for line in self:
            line.seller_discount_max = line._get_seller_discount_max()

    @api.depends("order_id")
    def _compute_seller_markup_max(self):
        markup_max = get_seller_markup_max_pct(self.env)
        for line in self:
            line.seller_markup_max = markup_max

    @api.depends(
        "seller_discount",
        "extra_discount",
        "product_id",
        "product_uom",
        "product_uom_qty",
        "order_id.commercial_condition_id",
        "order_id.commercial_condition_id.seller_discount",
        "order_id.commercial_condition_id.line_ids.seller_discount",
        "order_id.commercial_condition_id.line_ids.extra_discount",
        "order_id.commercial_condition_id.line_ids.band_ids",
        "order_id.commercial_condition_id.line_ids.band_ids.qty_min",
        "order_id.commercial_condition_id.line_ids.band_ids.qty_uom_id",
        "order_id.commercial_condition_id.line_ids.band_ids.seller_discount",
        "order_id.commercial_condition_id.line_ids.band_ids.extra_discount",
    )
    def _compute_discount_changed(self):
        for line in self:
            if not line.product_id or not line.order_id.commercial_condition_id:
                line.discount_changed = False
                continue
            (
                cond_seller,
                cond_extra,
                _source,
            ) = line.order_id._get_condition_discount_for_product(
                line.product_id,
                qty=line.product_uom_qty,
                uom=line.product_uom,
            )
            line.discount_changed = (line.seller_discount or 0.0) != (
                cond_seller or 0.0
            ) or (line.extra_discount or 0.0) != (cond_extra or 0.0)

    @api.depends("seller_discount", "extra_discount")
    def _compute_total_seller_extra_discount(self):
        for line in self:
            line.total_seller_extra_discount = (line.seller_discount or 0) + (
                line.extra_discount or 0
            )

    @api.depends(
        "seller_discount",
        "product_id",
        "product_uom",
        "product_uom_qty",
        "order_id.sales_profile_id",
        "order_id.sales_profile_id.rule_ids",
        "order_id.sales_profile_id.rule_ids.applied_on",
        "order_id.sales_profile_id.rule_ids.product_id",
        "order_id.sales_profile_id.rule_ids.product_tmpl_id",
        "order_id.sales_profile_id.rule_ids.categ_id",
        "order_id.sales_profile_id.rule_ids.commission_band_ids",
        "order_id.sales_profile_id.rule_ids.qty_min",
        "order_id.sales_profile_id.rule_ids.qty_uom_id",
        "order_id.sales_profile_id.rule_ids.sequence",
    )
    def _compute_commission_rate(self):
        for line in self:
            rate = line._get_policy_commission_rate()
            line.commission_rate = rate if rate is not False else 0.0

    @api.constrains("seller_discount")
    def _check_seller_discount_limit(self):
        self._validate_seller_discount_limit()

    def _validate_seller_discount_limit(self):
        """Check seller discount against the absolute max for each line.

        Global markup limit (``validate_seller_markup``) is always
        enforced. The comparison against the profile's
        ``seller_discount_max`` is skipped when the line resolved to a
        locked line (the director defined the discount, bypassing the
        rep's profile ceiling).
        """
        for line in self:
            # Markup always runs — locked does not bypass structural checks.
            validate_seller_markup(self.env, line.seller_discount)
            if line.locked_line_id:
                continue
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
        # Snapshot locked line on every covered line (even when vals
        # didn't bring seller/extra discount) so subsequent writes are
        # caught by _check_locked_line_edit. Without this, a rep could
        # create a line with no discount, leave locked_line_id empty,
        # then write a discount later and bypass the guard.
        lines._snapshot_locked_on_create()
        lines._check_locked_line_create(vals_list)
        lines._resolve_agent_commissions()
        return lines

    def write(self, vals):
        self._check_direct_price_edit(vals)
        self._check_locked_line_edit(vals)
        # Capture pre-write alignment for qty/uom/product changes so we
        # can re-apply the condition band without overwriting manual
        # override.
        band_triggers = {"product_uom_qty", "product_uom", "product_id"}
        old_alignment = {}
        if band_triggers & set(vals):
            old_alignment = {
                line.id: line._is_aligned_with_condition_band() for line in self
            }
        res = super().write(vals)
        if old_alignment:
            for line in self:
                if old_alignment.get(line.id):
                    line._reapply_condition_band()
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

        For agent profiles, ensures each agent line points to the managed
        commission matching the agent modality (invoice_state) and the
        effective rate from ``_get_policy_commission_rate``. A rate of 0%
        creates a managed commission that produces zero commission amount.

        ``_get_policy_commission_rate`` returns the snapshot Float
        ``locked_fixed_commission_rate`` for locked lines, else the
        band-resolved rate. Returns ``False`` only when no band matched
        on a regular line; in that case we skip the agent.
        """
        Commission = self.env["commission"]
        for line in self:
            profile = line.order_id.sales_profile_id
            if not profile or profile.profile_type != "agent":
                continue
            rate = line._get_policy_commission_rate()
            if rate is False:
                continue
            for agent_line in line.agent_ids:
                base_commission = agent_line.agent_id.commission_id
                commission = Commission._ensure_managed_commission(
                    base_commission.invoice_state, rate
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

    def _check_locked_line_edit(self, vals):
        """Block writes to seller_discount/extra_discount on lines under
        locked.

        Rep: blocked. Manager and director: allowed (pontual override, no
        audit trail per business decision). System (group_system):
        allowed. Programmatic apply/reapply bypass via
        ``tr_skip_locked_protection`` context flag.
        """
        protected = {"seller_discount", "extra_discount"}
        if not protected.intersection(vals):
            return
        if self.env.context.get("tr_skip_locked_protection"):
            return
        if self.env.user.has_group("base.group_system"):
            return
        if self.env.user.has_group("tr_commercial_policy.group_sales_manager"):
            return
        locked_lines = self.filtered("locked_line_id")
        if not locked_lines:
            return
        raise ValidationError(
            _(
                "Direct editing of seller/extra discount is not allowed "
                "on lines under a locked condition. Affected lines: %s",
                ", ".join(locked_lines.mapped("product_id.display_name")),
            )
        )

    def _check_locked_line_create(self, vals_list):
        """Block create of sale.order.line that brings seller/extra
        discount values diverging from the locked line baseline.

        Onchange/backfill flows legitimately pre-fill seller/extra
        discount with the values the policy produces — those must be
        accepted (they match the locked line). Only divergent values
        (rep trying to override at create time) are blocked.
        """
        if self.env.context.get("tr_skip_locked_protection"):
            return
        if self.env.user.has_group("base.group_system"):
            return
        if self.env.user.has_group("tr_commercial_policy.group_sales_manager"):
            return
        protected = {"seller_discount", "extra_discount"}
        blocked = self.browse()
        for record, vals in zip(self, vals_list):
            if not protected.intersection(vals.keys()):
                continue
            condition = record.order_id.commercial_condition_id
            if not condition or not record.product_id:
                continue
            (
                expected_seller,
                expected_extra,
                source,
                _locked,
                _rate,
            ) = condition._resolve_with_locked(
                record.product_id,
                qty=record.product_uom_qty or 0.0,
                uom=record.product_uom,
            )
            if source != "locked":
                continue
            given_seller = vals.get("seller_discount", record.seller_discount or 0.0)
            given_extra = vals.get("extra_discount", record.extra_discount or 0.0)
            precision = self.env["decimal.precision"].precision_get("Discount Policy")
            seller_diff = float_compare(
                given_seller or 0.0,
                expected_seller or 0.0,
                precision_digits=precision,
            )
            extra_diff = float_compare(
                given_extra or 0.0,
                expected_extra or 0.0,
                precision_digits=precision,
            )
            if seller_diff != 0 or extra_diff != 0:
                blocked |= record
        if blocked:
            raise ValidationError(
                _(
                    "Direct editing of seller/extra discount on lines "
                    "covered by a locked condition is not allowed. "
                    "Affected lines: %s",
                    ", ".join(blocked.mapped("product_id.display_name")),
                )
            )

    def _snapshot_locked_on_create(self):
        """Populate locked_line_id, locked_fixed_commission_rate and
        baseline discounts on any line whose product falls under an
        active locked line.

        Called right after super().create() so the snapshot exists
        regardless of whether the create vals went through the
        ``_apply_condition_to_line`` flow (which only runs in UI
        onchange). Without this, RPC create with no seller/extra
        discount would leave the snapshot empty and subsequent writes
        would slip past ``_check_locked_line_edit``.
        """
        for line in self:
            if line.locked_line_id:
                continue
            condition = line.order_id.commercial_condition_id
            if not condition or not line.product_id:
                continue
            (
                seller,
                extra,
                source,
                locked_line,
                fixed_rate,
            ) = condition._resolve_with_locked(
                line.product_id,
                qty=line.product_uom_qty or 0.0,
                uom=line.product_uom,
            )
            if source == "locked":
                line.with_context(tr_skip_locked_protection=True).write(
                    {
                        "locked_line_id": locked_line.id,
                        "locked_fixed_commission_rate": fixed_rate or 0.0,
                        "locked_baseline_seller_discount": seller or 0.0,
                        "locked_baseline_extra_discount": extra or 0.0,
                    }
                )

    def _get_policy_commission_rate(self):
        """Single source of truth for the line's commission under policy.

        When the line is under a locked line, the snapshot Float
        ``locked_fixed_commission_rate`` (stored at apply time) is the
        commission. Otherwise, falls back to the existing band-based
        resolution from the agent profile.
        """
        self.ensure_one()
        if self.locked_line_id:
            return self.locked_fixed_commission_rate or 0.0
        return self._get_commission_rate_from_bands()

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
            rate = self._get_policy_commission_rate()
            if rate is not False:
                base_commission = agent.commission_id
                if base_commission:
                    commission = self.env["commission"]._find_managed_commission(
                        base_commission.invoice_state, rate
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
                "locked_line_id": self.locked_line_id.id,
                "locked_fixed_commission_rate": self.locked_fixed_commission_rate,
                "locked_baseline_seller_discount": (
                    self.locked_baseline_seller_discount
                ),
                "locked_baseline_extra_discount": (self.locked_baseline_extra_discount),
            }
        )
        return vals

    def action_open_locked_line(self):
        """Open the locked line linked to this sale line for inspection.

        Director can edit; manager and rep land on readonly form (ACL).
        """
        self.ensure_one()
        if not self.locked_line_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": "partner.commercial.condition.locked.line",
            "res_id": self.locked_line_id.id,
            "view_mode": "form",
            "target": "new",
            "name": _("Locked Line"),
        }

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
        Within each level, the rule with the highest ``qty_min`` met by the
        line quantity wins. Returns the matching ``tr.sales.profile.rule``
        record or empty recordset.
        """
        self.ensure_one()
        profile = self.order_id.sales_profile_id
        if not profile or not self.product_id:
            return self.env["tr.sales.profile.rule"]
        return resolve_applicable_rule(
            self.product_id,
            profile.rule_ids,
            self.env["tr.sales.profile.rule"],
            self.product_uom_qty or 0.0,
            self.product_uom,
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

    # ------------------------------------------------------------------
    # Qty-band re-application (condition.line.band)
    # ------------------------------------------------------------------

    def _is_aligned_with_locked_snapshot(self):
        """Check alignment against the BASELINE discounts snapshotted
        on the record at apply time. Stable across edits of the
        locked line itself: changes to the locked record after the
        order is placed do not flip this flag — only the
        manager/director overriding discounts on the line does.

        Returns False when there is no locked snapshot — tier
        validation for those cases runs through the normal path.
        """
        self.ensure_one()
        if not self.locked_line_id:
            return False
        return (self.seller_discount or 0.0) == (
            self.locked_baseline_seller_discount or 0.0
        ) and (self.extra_discount or 0.0) == (
            self.locked_baseline_extra_discount or 0.0
        )

    def _is_aligned_with_condition_band(self):
        """Check if seller/extra match what the condition would produce
        for the line's CURRENT product/qty/uom.

        Used by ``write()`` BEFORE applying changes to detect manual
        override (so we don't overwrite it on qty/uom edits).
        """
        self.ensure_one()
        condition = self.order_id.commercial_condition_id
        if not condition or not self.product_id:
            return True  # No condition → "aligned" trivially.
        seller, extra, _src = condition._resolve_discount_for_product(
            self.product_id,
            qty=self.product_uom_qty,
            uom=self.product_uom,
        )
        return (self.seller_discount or 0.0) == (seller or 0.0) and (
            self.extra_discount or 0.0
        ) == (extra or 0.0)

    def _reapply_condition_band(self):
        """Re-apply seller/extra from condition without override check.

        Caller is responsible for deciding whether re-application is
        appropriate (e.g. user-was-aligned-pre-edit). Also refreshes the
        locked snapshot (line may have moved into/out of locked scope
        on qty/uom changes). Bypasses ``_check_locked_line_edit`` for the
        legitimate programmatic write.
        """
        for line in self:
            condition = line.order_id.commercial_condition_id
            if not condition or not line.product_id:
                continue
            (
                seller,
                extra,
                _src,
                locked_line,
                fixed_rate,
            ) = condition._resolve_with_locked(
                line.product_id,
                qty=line.product_uom_qty,
                uom=line.product_uom,
            )
            line.with_context(tr_skip_locked_protection=True).write(
                {
                    "seller_discount": seller,
                    "extra_discount": extra,
                    "locked_line_id": locked_line.id if locked_line else False,
                    "locked_fixed_commission_rate": fixed_rate or 0.0,
                    "locked_baseline_seller_discount": (
                        (seller or 0.0) if locked_line else 0.0
                    ),
                    "locked_baseline_extra_discount": (
                        (extra or 0.0) if locked_line else 0.0
                    ),
                }
            )

    @api.onchange("product_uom_qty", "product_uom")
    def _onchange_qty_uom_apply_band(self):
        """Re-apply condition band when qty/uom change in the form.

        Preserves manual override: only re-applies when ``_origin``
        (the persisted record) was aligned with the condition. New
        unsaved lines have empty ``_origin`` and re-apply normally.
        """
        for line in self:
            origin = line._origin
            if not origin or not origin.id:
                line._reapply_condition_band()
                continue
            if origin._is_aligned_with_condition_band():
                line._reapply_condition_band()

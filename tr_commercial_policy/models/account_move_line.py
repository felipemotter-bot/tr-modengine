# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .policy_utils import (
    calc_price_unit,
    calc_reference_price,
    get_policy_rates,
    resolve_applicable_rule,
)


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    seller_discount = fields.Float(
        string="Seller Discount (%)",
        digits="Discount Policy",
    )
    extra_discount = fields.Float(
        string="Extra Discount (%)",
        digits="Discount Policy",
    )
    extra_discount_reason = fields.Char()
    base_price = fields.Float(
        digits="Sale Price",
        compute="_compute_base_price",
        store=True,
        readonly=False,
        precompute=True,
    )
    reference_price = fields.Float(
        digits="Sale Price",
        compute="_compute_reference_price",
        store=True,
        readonly=False,
        precompute=True,
    )
    # Mirrors ``product_id.categ_id.allow_manual_price_edit`` so the view
    # can switch ``price_unit`` between readonly / editable via ``attrs``
    # (the backend ``_check_direct_price_edit`` uses the same flag).
    allow_manual_price_edit = fields.Boolean(
        related="product_id.categ_id.allow_manual_price_edit",
    )
    seller_discount_max = fields.Float(
        string="Max Seller Discount (%)",
        compute="_compute_seller_discount_max",
        help="Maximum seller discount from the effective sales profile.",
    )
    total_seller_extra_discount = fields.Float(
        string="Total Discount (%)",
        compute="_compute_total_seller_extra_discount",
        help="Sum of seller discount and extra discount.",
    )
    commission_rate = fields.Float(
        string="Commission (%)",
    )
    # Line-level mirrors of the invoice header's contractual return fields
    # so the line form can show a "Retorno Contratual" block matching the
    # sale order line form.
    contractual_return = fields.Float(
        related="move_id.tr_contractual_return",
        string="Contractual Return (%)",
    )
    adjustment_factor_display = fields.Char(
        related="move_id.adjustment_factor_display",
    )

    @api.depends("move_id.partner_id")
    def _compute_agent_ids(self):
        """Protect agent lines inherited from sale orders.

        Lines with sale_line_ids are in the "sale origin" regime and must
        not be recomputed by the OCA/engenere default flow.  This
        preserves the managed commission snapshot from the sale order.

        MRO: policy → engenere (protects reversals) → OCA (recomputes).
        """
        sale_origin = self.filtered(lambda line: line.sale_line_ids)
        remaining = self - sale_origin
        if remaining:
            return super(AccountMoveLine, remaining)._compute_agent_ids()
        return False

    # --- Manual invoice: base price ---

    @api.depends(
        "product_id",
        "quantity",
        "product_uom_id",
        "move_id.commercial_condition_id",
        "move_id.invoice_date",
        "move_id.date",
    )
    def _compute_base_price(self):
        """Resolve ``base_price`` from the effective pricelist.

        Only runs for draft invoice/refund lines that do NOT originate
        from a sale order (those keep the snapshot copied via
        ``_prepare_invoice_line``). Posted moves are frozen so the
        historical ``base_price`` captured at posting time is never
        rewritten by a later module update.

        Without this compute the field would stay at 0 after save
        because the XML renders it ``readonly`` and the UI drops
        read-only values from the submitted vals; the onchange's
        in-memory value would never reach ``create()``.
        """
        for line in self:
            if line.sale_line_ids:
                continue
            move = line.move_id
            if move.move_type not in ("out_invoice", "out_refund"):
                continue
            if move.state != "draft":
                continue
            line.base_price = line._compute_manual_base_price()

    @api.depends("base_price", "move_id.tr_contractual_return")
    def _compute_reference_price(self):
        """Derive ``reference_price`` from ``base_price`` + contractual return.

        Mirrors ``sale.order.line._compute_reference_price`` so the
        pricing chain stays coherent on manual invoice lines: when
        ``base_price`` is recomputed (quantity, UoM, date, condition
        change), ``reference_price`` follows, and ``_compute_price_unit``
        then uses the fresh value.

        Sale-origin lines keep the snapshot copied via
        ``_prepare_invoice_line`` and are never touched here.
        Posted moves are frozen.
        """
        tax_rate, freight_rate, admin_rate = get_policy_rates(self.env)
        for line in self:
            if line.sale_line_ids:
                continue
            move = line.move_id
            if move.move_type not in ("out_invoice", "out_refund"):
                continue
            if move.state != "draft":
                continue
            cr = move.tr_contractual_return or 0.0
            line.reference_price = calc_reference_price(
                line.base_price, cr, tax_rate, freight_rate, admin_rate
            )

    def _compute_manual_base_price(self):
        """Resolve base_price from the condition's pricelist.

        Replicates the sale order chain: pricelist price → tax-included
        unit price (handles currency, fiscal position, included taxes).
        """
        self.ensure_one()
        pricelist = self.move_id._get_effective_pricelist()
        if not pricelist or not self.product_id:
            return 0.0
        move = self.move_id
        move_date = move.invoice_date or move.date or fields.Date.context_today(move)
        raw_price = pricelist._get_product_price(
            self.product_id,
            self.quantity or 1.0,
            uom=self.product_uom_id,
            date=move_date,
        )
        return self.product_id._get_tax_included_unit_price(
            self.company_id,
            move.currency_id,
            move_date,
            "sale",
            fiscal_position=move.fiscal_position_id,
            product_price_unit=raw_price,
            product_currency=pricelist.currency_id,
        )

    # Mirrors the ``sale.order.line._compute_price_unit`` pattern: apply
    # the policy chain (``reference_price`` → discounts) instead of the
    # product's plain tax-included unit price. Explicit depends so the
    # form recalculates ``price_unit`` live when the user touches the
    # quantity / discounts (the base compute on account.move.line has
    # no depends, only ``precompute=True``, which only fires on create).
    @api.depends(
        "reference_price",
        "seller_discount",
        "extra_discount",
        "product_id",
        # Preserve the base Odoo depend on ``product_uom_id`` — the
        # fallback path delegates to ``super()`` and the base compute
        # uses UoM to resolve the tax-included unit price.
        "product_uom_id",
    )
    def _compute_price_unit(self):
        # During snapshot / clear writes the move header toggles
        # ``tr_skip_manual_snapshot`` exactly to stop the backend
        # from reacting to its own syncing writes. ``_prepare_clear_line_vals``
        # sets ``price_unit=0`` explicitly; any compute running in this
        # window would fight the intended clear (either zeroing again
        # after ``super`` or recomputing from a half-updated policy
        # chain). No-op is the safe behavior.
        if self.env.context.get("tr_skip_manual_snapshot"):
            return True
        remaining = self.browse()
        for line in self:
            move = line.move_id
            if line.sale_line_ids:
                continue
            if move.move_type not in ("out_invoice", "out_refund"):
                remaining |= line
                continue
            if move.state != "draft":
                continue
            # Gate: only steer ``price_unit`` when the line actually
            # participates in the commercial policy. A manual out_invoice
            # created by a user who did not opt into the policy (no
            # ``commercial_condition_id`` on the move, no policy-level
            # fields populated on the line) must keep whatever the user
            # wrote — otherwise this override sequesters ``price_unit``
            # on every quantity edit and resets it to
            # ``calc_price_unit(0, 0, 0) == 0``.
            #
            # This gate is additive to the context skip above: ``tr_skip_manual_snapshot``
            # handles the intentional-clear flow (where ``base_price`` etc
            # are zeroed on purpose); this gate handles the never-touched
            # flow (where the user set ``price_unit`` and nothing else).
            policy_applied = (
                move.commercial_condition_id
                or line.base_price
                or line.reference_price
                or line.seller_discount
                or line.extra_discount
            )
            if not policy_applied:
                remaining |= line
                continue
            # Draft sales invoice line under policy: the policy owns
            # ``price_unit`` entirely, even when ``reference_price`` is
            # zero. A zero reference price here is the legitimate signal
            # for "condition explicitly cleared" — the context skip above
            # already protected ``_prepare_clear_line_vals``, so if we
            # reach here with zero ref price, the caller meant it.
            line.price_unit = calc_price_unit(
                line.reference_price,
                line.seller_discount,
                line.extra_discount,
            )
        if remaining:
            return super(AccountMoveLine, remaining)._compute_price_unit()
        return True

    # --- Onchange ---

    @api.onchange("product_id")
    def _onchange_product_apply_condition(self):
        """Prefill policy fields from condition when product is set."""
        if not self.product_id or self.sale_line_ids:
            return
        move = self.move_id
        if not move.commercial_condition_id:
            return
        line_vals = move._prepare_condition_line_vals(
            self, move.commercial_condition_id
        )
        self.update(line_vals)
        move._resolve_manual_invoice_agent_commissions()

    @api.depends("seller_discount", "extra_discount")
    def _compute_total_seller_extra_discount(self):
        for line in self:
            line.total_seller_extra_discount = (line.seller_discount or 0) + (
                line.extra_discount or 0
            )

    @api.depends(
        "product_id",
        "move_id.sales_profile_id",
        "move_id.move_type",
    )
    def _compute_seller_discount_max(self):
        for line in self:
            if (
                line.move_id.move_type not in ("out_invoice", "out_refund")
                or not line.product_id
                or not line.move_id.sales_profile_id
            ):
                line.seller_discount_max = 0.0
                continue
            line.seller_discount_max = line._get_seller_discount_absolute_max()

    @api.onchange("seller_discount", "extra_discount")
    def _onchange_seller_extra_discount(self):
        """Recalculate price_unit and commission_rate from discounts.

        Cirúrgico: only touches price_unit + commission_rate.
        Does NOT modify agent_ids, header fields, or trigger resync.
        """
        for line in self:
            if not line.reference_price:
                continue
            line.price_unit = calc_price_unit(
                line.reference_price,
                line.seller_discount,
                line.extra_discount,
            )
            rate = line._get_commission_rate_for_discount(line.seller_discount)
            line.commission_rate = rate if rate is not False else 0.0

    # --- Constraints ---

    @api.constrains("seller_discount")
    def _check_seller_discount_limit(self):
        self._validate_seller_discount_limit()

    def _validate_seller_discount_limit(self):
        """Check seller discount against the absolute max for each line.

        Uses _get_seller_discount_absolute_max (highest band limit).
        Band-contextual checks are handled by tier validation.
        """
        for line in self:
            if not line.move_id.sales_profile_id:
                continue
            max_disc = line._get_seller_discount_absolute_max()
            if max_disc and line.seller_discount > max_disc:
                raise ValidationError(
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
            if not line.move_id.sales_profile_id:
                continue
            if line.extra_discount < 0:
                raise ValidationError(_("Extra discount cannot be negative."))
            if line.extra_discount > 99:
                raise ValidationError(_("Extra discount cannot exceed 99%%."))
            total = (line.seller_discount or 0) + (line.extra_discount or 0)
            if total > 99:
                raise ValidationError(
                    _(
                        "Total discount (seller %(seller).2f%% + extra"
                        " %(extra).2f%% = %(total).2f%%) cannot"
                        " exceed 99%%."
                    )
                    % {
                        "seller": line.seller_discount,
                        "extra": line.extra_discount,
                        "total": total,
                    }
                )

    # --- Rule resolution (duplicated from sale.order.line) ---

    def _get_applicable_rule(self, profile=None):
        """Resolve the most specific profile rule for this line's product.

        Resolution order: product variant > product template > category > general.
        Returns the matching tr.sales.profile.rule record or empty recordset.

        If ``profile`` is provided, uses it directly instead of
        resolving from move (avoids stale values during writes).

        IMPORTANT: This method must remain semantically equivalent to
        sale.order.line._get_applicable_rule().  Any change there must
        be reflected here.  Test: test_invoice_line_rule_resolution_matches_sale_line.
        """
        self.ensure_one()
        if profile is None:
            profile = self.move_id._get_effective_sales_profile()
        if not profile or not self.product_id:
            return self.env["tr.sales.profile.rule"]
        return resolve_applicable_rule(
            self.product_id, profile.rule_ids, self.env["tr.sales.profile.rule"]
        )

    def _get_seller_discount_absolute_max(self):
        """Return the absolute maximum seller discount for the profile rule.

        Returns the highest possible limit (highest band's max).
        Used by _validate_seller_discount_limit so that band-contextual
        checks are left to tier validation.
        """
        # Same logic as sale.order.line._get_seller_discount_absolute_max
        self.ensure_one()
        rule = self._get_applicable_rule()
        if not rule:
            return 0.0
        if rule.order_value_band_ids:
            return max(rule.order_value_band_ids.mapped("seller_discount_max"))
        return rule.seller_discount_max

    def _get_commission_rate_for_discount(self, seller_discount, profile=None):
        """Resolve commission rate from bands for a given discount.

        Finds the first band where discount_up_to >= seller_discount
        and returns its commission_rate.  Returns False when no
        applicable band matches.

        If ``profile`` is provided, uses it directly instead of
        resolving from move (avoids stale values during writes).
        """
        self.ensure_one()
        rule = self._get_applicable_rule(profile=profile)
        if not rule or not rule.commission_band_ids:
            return False
        seller_disc = seller_discount or 0.0
        bands = rule.commission_band_ids.sorted("discount_up_to")
        for band in bands:
            if band.discount_up_to >= seller_disc:
                return band.commission_rate
        return False

    # --- Write override ---

    def _check_direct_price_edit(self, vals):
        """Block direct writes to price_unit/discount on out_invoice lines.

        Respects category exception (allow_manual_price_edit), admin bypass,
        and context flag.  Does NOT apply to out_refund or other move types.

        The category exception only applies to manual invoice lines (no
        sale origin).  Sale-origin lines must keep parity with the order
        — the snapshot check enforces this at post time.
        """
        protected_fields = {"price_unit", "discount"}
        if not protected_fields.intersection(vals):
            return
        if self.env.context.get("tr_skip_price_protection"):
            return
        if self.env.user.has_group("base.group_system"):
            return
        for line in self:
            if line.move_id.move_type != "out_invoice":
                continue
            if line.display_type != "product" or not line.product_id:
                continue
            # Category exception: only for manual lines (no sale origin)
            if (
                not line.sale_line_ids
                and line.product_id.categ_id.sudo().allow_manual_price_edit
            ):
                continue
            raise ValidationError(
                _(
                    "Direct editing of price and discount is not allowed "
                    "on invoice lines. Use seller discount and extra "
                    "discount fields instead."
                )
            )

    def button_edit_agents(self):
        """Open agent editor — readonly for non-directors."""
        action = super().button_edit_agents()
        if not self.env.user.has_group("tr_commercial_policy.group_sales_director"):
            action["flags"] = {"mode": "readonly"}
        return action

    def write(self, vals):
        self._check_direct_price_edit(vals)
        result = super().write(vals)
        if {"seller_discount", "extra_discount"} & set(vals):
            moves_to_sync = self.mapped("move_id").filtered("review_ids")
            for move in moves_to_sync:
                move._sync_discount_validation_state()
        return result

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import float_compare

from .policy_utils import (
    calc_price_unit,
    calc_reference_price,
    get_policy_rates,
    get_seller_markup_max_pct,
    resolve_applicable_rule,
    validate_seller_markup,
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
    seller_markup_max = fields.Float(
        string="Max Seller Markup (%)",
        compute="_compute_seller_markup_max",
        digits="Discount Policy",
        help="Maximum markup (negative seller discount) allowed globally.",
    )
    total_seller_extra_discount = fields.Float(
        string="Total Discount (%)",
        compute="_compute_total_seller_extra_discount",
        help="Sum of seller discount and extra discount.",
    )
    commission_rate = fields.Float(
        string="Commission (%)",
    )
    locked_condition_line_id = fields.Many2one(
        comodel_name="partner.commercial.condition.line",
        string="Locked Condition Line",
        readonly=True,
        index=True,
        ondelete="restrict",
        help="Snapshot of the locked condition.line that resolved for "
        "this product. Propagated from the sale order line (when from "
        "sale) or filled at apply time (manual invoice).",
    )
    locked_fixed_commission_rate = fields.Float(
        string="Locked Fixed Commission (%)",
        readonly=True,
        digits="Discount Policy",
        help="Snapshot of the locked line's fixed_commission_rate at "
        "the moment the condition was applied / propagated from sale.",
    )
    locked_baseline_seller_discount = fields.Float(
        string="Locked Baseline Seller Discount (%)",
        readonly=True,
        digits="Discount Policy",
        help="Snapshot of the seller discount produced by the locked "
        "line at apply / propagation time.",
    )
    locked_baseline_extra_discount = fields.Float(
        string="Locked Baseline Extra Discount (%)",
        readonly=True,
        digits="Discount Policy",
        help="Snapshot of the extra discount produced by the locked "
        "line at apply / propagation time.",
    )
    tr_locked_readonly_for_user = fields.Boolean(
        compute="_compute_tr_locked_readonly_for_user",
        help="UI helper: True when the line is under a locked condition "
        "AND the current user is NOT a sales manager (or above). Drives "
        "the ``readonly`` attribute on discount fields and hides the "
        "discount-related buttons. Server-side guards remain the "
        "authoritative check.",
    )

    @api.depends("locked_condition_line_id")
    @api.depends_context("uid")
    def _compute_tr_locked_readonly_for_user(self):
        is_manager = self.env.user.has_group("tr_commercial_policy.group_sales_manager")
        for line in self:
            line.tr_locked_readonly_for_user = (
                bool(line.locked_condition_line_id) and not is_manager
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
            # Sale-origin lines are no longer skipped here. ``seller_discount``
            # and ``extra_discount`` are Classe A (own-rule) in
            # ``OWN_RULE_KINDS`` — editable in draft and revalidated at post.
            # If we kept the skip, the backend would restore ``price_unit``
            # to ``super()``'s pricelist value, silently undoing the
            # operator's edit and breaking ``_get_invoice_snapshot_issues``
            # (which compares against ``calc_price_unit`` of the line's own
            # reference / seller / extra). The previous skip was carried
            # over from the snapshot-rigid origin of the module; today the
            # taxonomy and the divergence banner explicitly support these
            # edits.
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
        "product_uom_id",
        "quantity",
        "move_id.sales_profile_id",
        "move_id.sales_profile_id.rule_ids",
        "move_id.sales_profile_id.rule_ids.applied_on",
        "move_id.sales_profile_id.rule_ids.product_id",
        "move_id.sales_profile_id.rule_ids.product_tmpl_id",
        "move_id.sales_profile_id.rule_ids.categ_id",
        "move_id.sales_profile_id.rule_ids.qty_min",
        "move_id.sales_profile_id.rule_ids.qty_uom_id",
        "move_id.sales_profile_id.rule_ids.sequence",
        "move_id.sales_profile_id.rule_ids.seller_discount_max",
        "move_id.sales_profile_id.rule_ids.order_value_band_ids",
        "move_id.sales_profile_id.rule_ids.order_value_band_ids.seller_discount_max",
        "move_id.sales_profile_id.rule_ids.commission_band_ids",
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

    @api.depends("move_id")
    def _compute_seller_markup_max(self):
        markup_max = get_seller_markup_max_pct(self.env)
        for line in self:
            line.seller_markup_max = markup_max

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
            rate = line._get_policy_commission_rate()
            line.commission_rate = rate if rate is not False else 0.0

    # --- Constraints ---

    @api.constrains("seller_discount")
    def _check_seller_discount_limit(self):
        self._validate_seller_discount_limit()

    def _validate_seller_discount_limit(self):
        """Check seller discount against the absolute max for each line.

        Markup global always runs. Skips comparison against
        ``seller_discount_max`` of the profile when the line is under
        a locked condition (director defined the discount).
        """
        for line in self:
            validate_seller_markup(self.env, line.seller_discount)
            if line.locked_condition_line_id:
                continue
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
            self.product_id,
            profile.rule_ids,
            self.env["tr.sales.profile.rule"],
            self.quantity or 0.0,
            self.product_uom_id,
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
        seller_disc = max(seller_discount or 0.0, 0.0)
        bands = rule.commission_band_ids.sorted("discount_up_to")
        for band in bands:
            if band.discount_up_to >= seller_disc:
                return band.commission_rate
        return False

    def _get_policy_commission_rate(self):
        """Single source of truth for commission on the invoice line.

        Mirrors ``sale.order.line._get_policy_commission_rate``:
        snapshot ``locked_fixed_commission_rate`` when the line is
        under a locked condition; otherwise band-resolved rate from
        the profile.
        """
        self.ensure_one()
        if self.locked_condition_line_id:
            return self.locked_fixed_commission_rate or 0.0
        return self._get_commission_rate_for_discount(self.seller_discount)

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

    def _check_locked_line_edit(self, vals):
        """Mirror of ``sale.order.line._check_locked_line_edit`` for
        invoice lines.

        Discount fields are validated against the BASELINE snapshot
        stored on the line when present; scope changes that move the
        line into locked are validated against ``_resolve_with_locked``
        live (only explicit discount in vals). Plain discount writes
        on snapshot-less lines pass through (decision 8 of the plan).
        """
        protected = {"seller_discount", "extra_discount"}
        scope_triggers = {"product_id", "product_uom_id", "quantity"}
        if not protected.intersection(vals) and not scope_triggers.intersection(vals):
            return
        if self.env.su:
            return
        if self.env.user.has_group("base.group_system"):
            return
        if self.env.user.has_group("tr_commercial_policy.group_sales_manager"):
            return
        precision = self.env["decimal.precision"].precision_get("Discount Policy")
        for line in self:
            if line.move_id.move_type not in ("out_invoice", "out_refund"):
                continue
            given_seller = vals.get("seller_discount", line.seller_discount or 0.0)
            given_extra = vals.get("extra_discount", line.extra_discount or 0.0)
            if line.locked_condition_line_id:
                if (
                    float_compare(
                        given_seller,
                        line.locked_baseline_seller_discount or 0.0,
                        precision_digits=precision,
                    )
                    != 0
                    or float_compare(
                        given_extra,
                        line.locked_baseline_extra_discount or 0.0,
                        precision_digits=precision,
                    )
                    != 0
                ):
                    raise ValidationError(
                        _(
                            "Cannot edit seller/extra discount on locked "
                            "invoice line — value diverges from the "
                            "snapshot baseline. Product: %s",
                            line.product_id.display_name,
                        )
                    )
                continue
            if not scope_triggers.intersection(vals):
                continue
            condition = line.move_id.commercial_condition_id
            if not condition:
                continue
            product = self.env["product.product"].browse(
                vals.get("product_id", line.product_id.id)
            )
            if not product:
                continue
            qty = vals.get("quantity", line.quantity or 0.0)
            uom = self.env["uom.uom"].browse(
                vals.get("product_uom_id", line.product_uom_id.id)
            )
            (
                expected_seller,
                expected_extra,
                source,
                _l,
                _r,
            ) = condition._resolve_with_locked(product, qty=qty, uom=uom)
            if source != "locked":
                continue
            if (
                "seller_discount" in vals
                and float_compare(
                    given_seller,
                    expected_seller or 0.0,
                    precision_digits=precision,
                )
                != 0
            ):
                raise ValidationError(
                    _(
                        "Scope change moves this invoice line under a "
                        "locked condition; explicit seller_discount in "
                        "vals must match canonical resolution. Product: %s",
                        product.display_name,
                    )
                )
            if (
                "extra_discount" in vals
                and float_compare(
                    given_extra,
                    expected_extra or 0.0,
                    precision_digits=precision,
                )
                != 0
            ):
                raise ValidationError(
                    _(
                        "Scope change moves this invoice line under a "
                        "locked condition; explicit extra_discount in "
                        "vals must match canonical resolution. Product: %s",
                        product.display_name,
                    )
                )

    def _check_locked_snapshot_write(self, vals):
        """Block direct writes to locked snapshot fields on existing
        invoice lines.

        Snapshot fields are internal metadata. ``action_resync_from_sale_order``
        and other legitimate flows that need to update snapshots on an
        existing invoice line use ``sudo()``. The sale→invoice
        propagation path is on ``create``, not ``write`` — kept out of
        this guard intentionally.
        """
        snapshot_fields = {
            "locked_condition_line_id",
            "locked_fixed_commission_rate",
            "locked_baseline_seller_discount",
            "locked_baseline_extra_discount",
        }
        if not snapshot_fields.intersection(vals):
            return
        if self.env.su:
            return
        if self.env.user.has_group("base.group_system"):
            return
        raise AccessError(
            _(
                "Locked snapshot fields are internal — they cannot be "
                "written directly on an invoice line. They are set "
                "automatically when the condition is applied or "
                "propagated from a sale order line."
            )
        )

    @staticmethod
    def _extract_sale_line_ids_from_vals(vals):
        """Normalize sale_line_ids M2M commands to a list of ids.

        Supports ``(4, id)`` (Command.LINK), ``(6, 0, [ids])``
        (Command.SET), and the equivalent ``Command.*`` wrappers.
        Ignores other commands.
        """
        commands = vals.get("sale_line_ids") or []
        ids = []
        for cmd in commands:
            if not isinstance(cmd, (list, tuple)) or len(cmd) < 2:
                continue
            op = cmd[0]
            if op == 4:
                ids.append(cmd[1])
            elif op == 6 and len(cmd) > 2:
                ids.extend(cmd[2] or [])
        return list(dict.fromkeys(ids))

    @staticmethod
    def _snapshots_match_sale_origin(vals, sale_line):
        """Return True when vals describe a verbatim propagation from
        the sale.order.line origin: product matches and the 4 snapshot
        fields match exactly.

        Product check defends against spoofing: rep cannot point
        ``sale_line_ids`` to a sale line of product X and use it to
        justify locked snapshots on an invoice line of product Y.
        Requires explicit ``product_id`` in vals matching the sale line
        — ausente conta como mismatch (defesa contra payload sem
        product_id seguido por write que define product_id de outro).
        """
        if not sale_line:
            return False
        # Product must match the sale line origin (defense against
        # cross-product snapshot spoof). Ausência conta como mismatch.
        if vals.get("product_id") != sale_line.product_id.id:
            return False
        # locked_condition_line_id
        given_id = vals.get(
            "locked_condition_line_id", sale_line.locked_condition_line_id.id or False
        )
        if (given_id or False) != (sale_line.locked_condition_line_id.id or False):
            return False
        # Float baselines and fixed_commission_rate: exact equality
        # acceptable because the propagation copies them verbatim.
        for field, source_val in (
            (
                "locked_fixed_commission_rate",
                sale_line.locked_fixed_commission_rate or 0.0,
            ),
            (
                "locked_baseline_seller_discount",
                sale_line.locked_baseline_seller_discount or 0.0,
            ),
            (
                "locked_baseline_extra_discount",
                sale_line.locked_baseline_extra_discount or 0.0,
            ),
        ):
            given_val = vals.get(field, source_val)
            if (given_val or 0.0) != (source_val or 0.0):
                return False
        return True

    def _check_locked_line_create(self, vals_list):
        """Mirror of ``sale.order.line._check_locked_line_create``.

        Special case: sale→invoice propagation. When vals carry
        ``sale_line_ids`` pointing to a single sale.order.line and the
        seller/extra discount in vals match the sale line origin, the
        invoice line is a verbatim copy of the sale snapshot. We MUST
        NOT validate against live ``_resolve_with_locked`` here — the
        director may have edited the locked condition.line after the
        order was placed, and the invoice line must honor the
        historical sale snapshot, not the current resolution.
        """
        if self.env.su:
            return
        if self.env.user.has_group("base.group_system"):
            return
        if self.env.user.has_group("tr_commercial_policy.group_sales_manager"):
            return
        protected = {"seller_discount", "extra_discount"}
        if not any(protected.intersection(v.keys()) for v in vals_list):
            return
        precision = self.env["decimal.precision"].precision_get("Discount Policy")
        for record, vals in zip(self, vals_list):
            if not protected.intersection(vals.keys()):
                continue
            if record.move_id.move_type not in ("out_invoice", "out_refund"):
                continue
            condition = record.move_id.commercial_condition_id
            if not condition or not record.product_id:
                continue
            # Propagation path: invoice line from a single sale.order.line
            # of the SAME product, with discount in vals matching the
            # sale line origin → accept without live re-resolution.
            sl_ids = self._extract_sale_line_ids_from_vals(vals)
            if len(sl_ids) == 1:
                sale_line = self.env["sale.order.line"].browse(sl_ids)
                product_id_in_vals = vals.get("product_id", record.product_id.id)
                if product_id_in_vals == sale_line.product_id.id:
                    given_seller = vals.get(
                        "seller_discount", record.seller_discount or 0.0
                    )
                    given_extra = vals.get(
                        "extra_discount", record.extra_discount or 0.0
                    )
                    if (
                        float_compare(
                            given_seller,
                            sale_line.seller_discount or 0.0,
                            precision_digits=precision,
                        )
                        == 0
                        and float_compare(
                            given_extra,
                            sale_line.extra_discount or 0.0,
                            precision_digits=precision,
                        )
                        == 0
                    ):
                        continue
            (
                expected_seller,
                expected_extra,
                source,
                _l,
                _r,
            ) = condition._resolve_with_locked(
                record.product_id,
                qty=record.quantity or 0.0,
                uom=record.product_uom_id,
            )
            if source != "locked":
                continue
            given_seller = vals.get("seller_discount", record.seller_discount or 0.0)
            given_extra = vals.get("extra_discount", record.extra_discount or 0.0)
            if (
                float_compare(
                    given_seller,
                    expected_seller or 0.0,
                    precision_digits=precision,
                )
                != 0
                or float_compare(
                    given_extra,
                    expected_extra or 0.0,
                    precision_digits=precision,
                )
                != 0
            ):
                raise ValidationError(
                    _(
                        "Cannot create invoice line with seller/extra "
                        "discount diverging from the canonical locked-"
                        "condition resolution. Product: %s",
                        record.product_id.display_name,
                    )
                )

    def _snapshot_locked_on_create(self, vals_list=None):
        """Populate locked snapshot fields + canonical discounts on
        invoice lines whose product falls under an active locked
        condition.line.

        Same semantics as ``sale.order.line._snapshot_locked_on_create``:
        applies discount canonically when the original vals did NOT
        carry seller/extra discount; otherwise leaves what was written
        (already validated by ``_check_locked_line_create``).

        Skip lines that come from a sale order (propagation path) —
        those already carry the historical snapshot from the sale
        line, must not be re-resolved against live condition.
        """
        vals_list = vals_list or [{} for _ in self]
        for line, vals in zip(self, vals_list):
            if line.locked_condition_line_id:
                continue
            if line.sale_line_ids:
                # Propagation from sale order — sale line is the
                # source of truth, do not re-resolve live.
                continue
            if line.move_id.move_type not in ("out_invoice", "out_refund"):
                continue
            condition = line.move_id.commercial_condition_id
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
                qty=line.quantity or 0.0,
                uom=line.product_uom_id,
            )
            if source != "locked":
                continue
            line.sudo().update(
                {
                    "locked_condition_line_id": locked_line.id,
                    "locked_fixed_commission_rate": fixed_rate or 0.0,
                    "locked_baseline_seller_discount": seller or 0.0,
                    "locked_baseline_extra_discount": extra or 0.0,
                }
            )
            update = {}
            if "seller_discount" not in vals:
                update["seller_discount"] = seller or 0.0
            if "extra_discount" not in vals:
                update["extra_discount"] = extra or 0.0
            if update:
                line.with_context(tr_skip_price_protection=True).write(update)

    @api.model
    def _check_locked_snapshot_write_create(self, vals_list):
        """Reject create vals carrying snapshot fields directly,
        EXCEPT the sale→invoice propagation path validated by
        ``_check_locked_snapshot_write``.
        """
        if self.env.su:
            return
        if self.env.user.has_group("base.group_system"):
            return
        snapshot_fields = {
            "locked_condition_line_id",
            "locked_fixed_commission_rate",
            "locked_baseline_seller_discount",
            "locked_baseline_extra_discount",
        }
        for vals in vals_list:
            if not snapshot_fields.intersection(vals.keys()):
                continue
            # Propagation path: invoice line from a single sale.order.line
            # with snapshots matching origin.
            sl_ids = self._extract_sale_line_ids_from_vals(vals)
            if len(sl_ids) == 1:
                sale_line = self.env["sale.order.line"].browse(sl_ids)
                if self._snapshots_match_sale_origin(vals, sale_line):
                    continue
            raise AccessError(
                _(
                    "Locked snapshot fields are internal — they cannot "
                    "be written directly on create. They are populated "
                    "automatically when the condition is applied or "
                    "propagated from a sale order line."
                )
            )

    @api.model_create_multi
    def create(self, vals_list):
        self._check_locked_snapshot_write_create(vals_list)
        lines = super().create(vals_list)
        lines._snapshot_locked_on_create(vals_list)
        lines._check_locked_line_create(vals_list)
        return lines

    def write(self, vals):
        self._check_direct_price_edit(vals)
        self._check_locked_snapshot_write(vals)
        self._check_locked_line_edit(vals)
        # Capture pre-write alignment for qty/uom/product changes so we
        # can re-apply the condition band without overwriting manual
        # override (mirror of sale.order.line write override).
        triggers = {"quantity", "product_uom_id", "product_id"}
        old_alignment = {}
        if triggers & set(vals):
            for line in self.filtered(
                lambda li: li.move_id.move_type in ("out_invoice", "out_refund")
                and not li.sale_line_ids
                and li.product_id
            ):
                old_alignment[line.id] = line._is_aligned_with_condition_band()
        result = super().write(vals)
        # Post-write reapply when scope changed AND resulting scope is
        # under locked: ensures canonical discount + snapshot.
        if triggers & set(vals):
            for line in self.filtered(
                lambda li: li.move_id.move_type in ("out_invoice", "out_refund")
                and not li.sale_line_ids
                and li.product_id
            ):
                condition = line.move_id.commercial_condition_id
                if not condition:
                    continue
                _s, _e, source, *_ = condition._resolve_with_locked(
                    line.product_id,
                    qty=line.quantity,
                    uom=line.product_uom_id,
                )
                # New scope under locked → reapply canonical.
                # OR line had locked snapshot but new scope is regular
                # → reapply (clears snapshot, applies regular discount).
                if source == "locked" or line.locked_condition_line_id:
                    line._reapply_condition_band()
        if old_alignment:
            for line in self:
                if (
                    line.id in old_alignment
                    and old_alignment[line.id]
                    and not line.locked_condition_line_id
                ):
                    line._reapply_condition_band()
        if {"seller_discount", "extra_discount"} & set(vals):
            moves_to_sync = self.mapped("move_id").filtered("review_ids")
            for move in moves_to_sync:
                move._sync_discount_validation_state()
        return result

    @api.onchange("quantity", "product_uom_id")
    def _onchange_qty_uom_apply_band(self):
        """Re-apply condition band when qty/uom change in form.

        Manual-only invoice lines (no ``sale_line_ids``). Sale-origin
        lines inherit discounts from sale.order.line and shouldn't
        re-resolve here.
        """
        for line in self:
            if line.sale_line_ids or not line.product_id:
                continue
            move = line.move_id
            if move.move_type not in ("out_invoice", "out_refund"):
                continue
            origin = line._origin
            if not origin or not origin.id:
                line._reapply_condition_band()
                continue
            if origin._is_aligned_with_condition_band():
                line._reapply_condition_band()

    def _is_aligned_with_locked_snapshot(self):
        """Same semantics as ``sale.order.line._is_aligned_with_locked_snapshot``.

        Compares the invoice line discount against the baseline
        snapshot stored on the line, not against a live re-resolution.
        """
        self.ensure_one()
        if not self.locked_condition_line_id:
            return False
        return (self.seller_discount or 0.0) == (
            self.locked_baseline_seller_discount or 0.0
        ) and (self.extra_discount or 0.0) == (
            self.locked_baseline_extra_discount or 0.0
        )

    def _is_aligned_with_condition_band(self):
        """Same semantics as sale.order.line's helper, for invoice lines."""
        self.ensure_one()
        condition = self.move_id.commercial_condition_id
        if not condition or not self.product_id:
            return True
        seller, extra, _src = condition._resolve_discount_for_product(
            self.product_id,
            qty=self.quantity,
            uom=self.product_uom_id,
        )
        return (self.seller_discount or 0.0) == (seller or 0.0) and (
            self.extra_discount or 0.0
        ) == (extra or 0.0)

    def _reapply_condition_band(self):
        """Re-apply seller/extra from condition for current qty/uom.

        Snapshot fields are updated via ``sudo()`` (internal metadata).
        """
        for line in self:
            condition = line.move_id.commercial_condition_id
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
                qty=line.quantity,
                uom=line.product_uom_id,
            )
            line.seller_discount = seller
            line.extra_discount = extra
            line.sudo().update(
                {
                    "locked_condition_line_id": (
                        locked_line.id if locked_line else False
                    ),
                    "locked_fixed_commission_rate": fixed_rate or 0.0,
                    "locked_baseline_seller_discount": (
                        (seller or 0.0) if locked_line else 0.0
                    ),
                    "locked_baseline_extra_discount": (
                        (extra or 0.0) if locked_line else 0.0
                    ),
                }
            )

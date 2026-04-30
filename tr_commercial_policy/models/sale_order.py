# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import formatLang

from .policy_utils import (
    calc_adjustment_factor,
    calc_line_pricing,
    check_cash_discount_limit,
    check_fob_discount_limit,
    check_payment_term_limit,
    get_extra_discount_approval_level,
    get_policy_rates,
)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    sales_profile_id = fields.Many2one(
        comodel_name="tr.sales.profile",
        string="Sales Profile",
        compute="_compute_sales_profile_id",
        store=True,
    )
    commercial_condition_id = fields.Many2one(
        comodel_name="partner.commercial.condition",
        string="Commercial Condition",
        compute="_compute_commercial_condition_id",
        store=True,
        readonly=True,
    )
    commercial_condition_warning = fields.Char(
        compute="_compute_commercial_condition_warning",
    )
    pricelist_warning = fields.Char(
        compute="_compute_pricelist_warning",
    )
    unmanaged_commission_warning = fields.Char(
        compute="_compute_unmanaged_commission_warning",
    )
    cash_discount = fields.Float(
        string="Cash Discount (%)",
        compute="_compute_condition_discounts",
        store=True,
        readonly=False,
        digits="Discount Policy",
    )
    fob_discount = fields.Float(
        string="FOB Discount (%)",
        compute="_compute_condition_discounts",
        store=True,
        readonly=False,
        digits="Discount Policy",
    )
    contractual_return = fields.Float(
        string="Contractual Return (%)",
        compute="_compute_contractual_return",
        store=True,
        readonly=True,
    )
    punctuality_discount = fields.Float(
        compute="_compute_punctuality_discount",
        store=True,
        readonly=True,
    )
    discount_rate = fields.Float(
        string="Order Discount (%)",
        digits="Discount Policy",
        help=(
            "Total discount applied to the order (cash discount + FOB discount)."
            " This discount is applied after the line unit price and is not"
            " related to seller or extra discounts on individual lines."
        ),
    )
    amount_discount_value = fields.Monetary(
        string="Order Discount Value",
        help=(
            "Total monetary value of the order discount (cash discount + FOB"
            " discount). Corresponds to the Order Discount (%) applied to"
            " each line."
        ),
    )

    @api.depends("contractual_return")
    def _compute_punctuality_discount(self):
        for order in self:
            if order.state in ("sale", "done"):
                continue
            order.punctuality_discount = order.contractual_return

    adjustment_factor = fields.Float(
        string="Adjustment Factor (%)",
        compute="_compute_adjustment_factor",
        help="Price adjustment percentage due to contractual return.",
    )
    discount_approval_level = fields.Selection(
        selection=[
            ("none", "None"),
            ("manager", "Manager"),
            ("director", "Director"),
        ],
        compute="_compute_discount_approval_level",
        store=True,
        help="Required approval level based on discount policy violations.",
    )
    discount_approval_snapshot = fields.Text(
        string="Discount Approval Details",
        compute="_compute_discount_approval_level",
        store=True,
        help="Current policy violations requiring approval.",
    )
    discount_display = fields.Selection(
        related="commercial_condition_id.discount_display",
        readonly=True,
    )
    payment_term_avg_days = fields.Float(
        compute="_compute_payment_term_avg_days",
    )

    @api.depends("contractual_return")
    def _compute_adjustment_factor(self):
        tax_rate, freight_rate, admin_rate = get_policy_rates(self.env)
        for order in self:
            order.adjustment_factor = calc_adjustment_factor(
                order.contractual_return, tax_rate, freight_rate, admin_rate
            )

    @api.depends("commercial_condition_id.applicable_profile_id")
    def _compute_sales_profile_id(self):
        for order in self:
            if order.state not in ("draft", "sent"):
                order.sales_profile_id = order.sales_profile_id
                continue
            order.sales_profile_id = (
                order.commercial_condition_id.applicable_profile_id or False
            )

    @api.depends("partner_id", "partner_id.effective_condition_id", "company_id")
    def _compute_commercial_condition_id(self):
        for order in self:
            if order.state not in ("draft", "sent"):
                order.commercial_condition_id = order.commercial_condition_id
                continue
            if order.partner_id:
                # ``effective_condition_id`` reads ``commercial_condition_id``
                # which is ``company_dependent=True``; without ``with_company``
                # the compute resolves against ``env.company`` and may pick
                # the wrong company's condition (regression observed: order
                # in TREINAMENTO picking up TRENTO's condition because the
                # compute fired with ``env.company`` = TRENTO).
                order.commercial_condition_id = order.partner_id.with_company(
                    order.company_id
                ).effective_condition_id
            else:
                order.commercial_condition_id = False

    @api.depends("partner_id", "commercial_condition_id")
    def _compute_commercial_condition_warning(self):
        for order in self:
            if order.partner_id and not order.commercial_condition_id:
                order.commercial_condition_warning = _(
                    "Customer '%s' has no commercial condition defined.",
                    order.partner_id.display_name,
                )
            else:
                order.commercial_condition_warning = False

    @api.depends("pricelist_id", "sales_profile_id", "sales_profile_id.pricelist_ids")
    def _compute_pricelist_warning(self):
        for order in self:
            profile = order.sales_profile_id
            if (
                profile
                and profile.pricelist_ids
                and order.pricelist_id
                and order.pricelist_id not in profile.pricelist_ids
            ):
                order.pricelist_warning = _(
                    "The pricelist '%(pricelist)s' is not allowed by the"
                    " sales profile '%(profile)s'.",
                    pricelist=order.pricelist_id.display_name,
                    profile=profile.display_name,
                )
            else:
                order.pricelist_warning = False

    # Best-effort informational banner. The previous depends tree
    # reached into ``sales_profile_id.rule_ids.commission_band_ids``
    # and ``order_line.agent_ids.commission_id`` — edges that inflate
    # the Odoo 16 ``transitive_triggers`` cold-start graph without
    # proportional UX gain.
    #
    # ``@api.depends("order_line")`` reacts to the relation (add /
    # remove / relink of lines) but NOT to writes on line fields
    # such as ``seller_discount`` or to changes on the referenced
    # profile / commission records. Banner may therefore go stale
    # between saves; a reload / save refreshes it.
    @api.depends("state", "order_line")
    def _compute_unmanaged_commission_warning(self):
        for order in self:
            profile = order.sales_profile_id
            if not profile or profile.profile_type != "agent":
                order.unmanaged_commission_warning = False
                continue
            recalculable = []
            outside_bands = []
            for line in order.order_line.filtered(lambda sol: sol.product_id):
                # Skip unsaved lines: ``_prepare_agent_vals`` deliberately
                # leaves ``commission_id`` as the agent's default (non-managed)
                # while the line is still a ``NewId``; the managed commission
                # is created post-save by ``_resolve_agent_commissions``. Were
                # we to evaluate these lines here, the banner would always
                # flag the line as "unmanaged" during editing and disappear
                # on the very first save — a false positive.
                if not isinstance(line.id, int):
                    continue
                has_unmanaged = any(
                    not a.commission_id.tr_managed for a in line.agent_ids
                )
                if not has_unmanaged:
                    continue
                if line._get_commission_rate_from_bands() is False:
                    outside_bands.append(line.product_id.display_name)
                else:
                    recalculable.append(line.product_id.display_name)
            messages = []
            if recalculable:
                messages.append(
                    _(
                        "Some lines have commissions not managed by the"
                        " commercial policy: %s."
                        " Use 'Recalculate Commissions' to fix,"
                        " or a director can confirm as-is."
                    )
                    % ", ".join(recalculable)
                )
            if outside_bands:
                messages.append(
                    _(
                        "These lines have a seller discount outside the"
                        " configured commission bands: %s."
                        " Add a band that covers the discount or adjust the"
                        " discount before confirming."
                    )
                    % ", ".join(outside_bands)
                )
            order.unmanaged_commission_warning = (
                "\n".join(messages) if messages else False
            )

    @api.depends("commercial_condition_id")
    def _compute_condition_discounts(self):
        """Seed editable cash/fob discount fields from the commercial
        condition. These two are ``readonly=False`` snapshots: the user
        can override them on the order, and later changes to the
        condition's own cash/fob discounts must not bleed back into
        existing orders — the reload wizard is the explicit boundary
        for re-syncing an order with its condition.
        """
        for order in self:
            if order.state not in ("draft", "sent"):
                continue
            condition = order.commercial_condition_id
            if condition:
                order.cash_discount = condition.cash_discount or 0.0
                order.fob_discount = condition.fob_discount or 0.0
            else:
                order.cash_discount = 0.0
                order.fob_discount = 0.0
            order.discount_rate = (order.cash_discount or 0) + (order.fob_discount or 0)

    @api.depends("commercial_condition_id")
    def _compute_contractual_return(self):
        """Dedicated compute for ``contractual_return``.

        Kept separate from :meth:`_compute_condition_discounts` because
        ``contractual_return`` is ``readonly=True``: the form never
        submits its value on save, while ``cash_discount`` and
        ``fob_discount`` do. When the three shared the same compute and
        the ORM saw the editable siblings arrive in the create ``vals``,
        it skipped the compute entirely, leaving ``contractual_return``
        NULL. Isolating it guarantees the ORM always recomputes this
        field when ``commercial_condition_id`` is (re)assigned.

        Depends only on ``commercial_condition_id`` (assignment), not
        on its value, so changes to the condition's ``contractual_return``
        do not bleed back into existing orders — the reload wizard
        is the explicit boundary for re-syncing an order with its
        condition (see ``action_reload_conditions``).
        """
        for order in self:
            if order.state not in ("draft", "sent"):
                continue
            if order.commercial_condition_id:
                order.contractual_return = (
                    order.commercial_condition_id.contractual_return or 0.0
                )
            else:
                order.contractual_return = 0.0

    def _compute_pricelist_id(self):
        result = super()._compute_pricelist_id()
        for order in self:
            if (
                order.commercial_condition_id
                and order.commercial_condition_id.pricelist_id
            ):
                order.pricelist_id = order.commercial_condition_id.pricelist_id
        return result

    @api.depends("payment_term_id")
    def _compute_payment_term_avg_days(self):
        for order in self:
            term = order.payment_term_id
            if not term or not term.line_ids:
                order.payment_term_avg_days = 0.0
                continue
            total_days = 0.0
            total_weight = 0.0
            for term_line in term.line_ids:
                days = term_line.months * 30 + term_line.days
                if term_line.value == "balance":
                    weight = 100.0 - total_weight
                else:
                    weight = term_line.value_amount
                total_days += days * weight
                total_weight += weight
            order.payment_term_avg_days = (
                total_days / total_weight if total_weight else 0.0
            )

    @api.onchange("cash_discount", "fob_discount")
    def _onchange_cash_fob_discount(self):
        """Recalculate discount on lines when cash/fob changes."""
        if self.commercial_condition_id:
            self.discount_rate = (self.cash_discount or 0) + (self.fob_discount or 0)
        if not self.sales_profile_id:
            return
        tax_rate, freight_rate, admin_rate = get_policy_rates(self.env)
        for line in self.order_line:
            if not line.product_id:
                continue
            result = calc_line_pricing(
                line.base_price,
                self.contractual_return,
                line.seller_discount,
                line.extra_discount,
                self.cash_discount,
                self.fob_discount,
                line.product_uom_qty,
                tax_rate,
                freight_rate,
                admin_rate,
            )
            line.discount = result["discount"]
            line.discount_value = result["discount_value"]

    @api.onchange("commercial_condition_id")
    def _onchange_commercial_condition_id(self):
        condition = self.commercial_condition_id
        if condition and condition.pricelist_id:
            self.pricelist_id = condition.pricelist_id
        for line in self.order_line:
            self._apply_condition_to_line(line, condition)

    def action_reload_conditions(self):
        """Open wizard showing what will change before reloading."""
        self.ensure_one()
        condition = self.commercial_condition_id
        if not condition:
            raise UserError(_("Cannot reload: customer has no commercial condition."))

        # Build wizard values
        def fmt(val):
            return formatLang(self.env, val, digits=2)

        vals = {
            "order_id": self.id,
        }
        # Pricelist change
        cond_pl = condition.pricelist_id
        if cond_pl and cond_pl != self.pricelist_id:
            vals[
                "pricelist_change"
            ] = f"{self.pricelist_id.display_name or '-'} → {cond_pl.display_name}"
        # Cash discount change
        if self.cash_discount != condition.cash_discount:
            vals[
                "cash_discount_change"
            ] = f"{fmt(self.cash_discount)}% → {fmt(condition.cash_discount)}%"
        # FOB discount change
        if self.fob_discount != condition.fob_discount:
            vals[
                "fob_discount_change"
            ] = f"{fmt(self.fob_discount)}% → {fmt(condition.fob_discount)}%"
        # Contractual return change
        if self.contractual_return != condition.contractual_return:
            current = fmt(self.contractual_return)
            new = fmt(condition.contractual_return)
            vals["contractual_return_change"] = f"{current}% → {new}%"

        wizard = self.env["tr.reload.condition.wizard"].create(vals)

        # Line-level changes
        for line in self.order_line.filtered(lambda sol: sol.product_id):
            changes = self._get_reload_line_changes(line, condition)
            if changes:
                self.env["tr.reload.condition.wizard.line"].create(
                    {
                        "wizard_id": wizard.id,
                        "sale_line_id": line.id,
                        "product_id": line.product_id.id,
                        "line_description": line.product_id.display_name,
                        "change_description": "\n".join(changes),
                    }
                )

        return {
            "type": "ir.actions.act_window",
            "res_model": "tr.reload.condition.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
            "name": _("Reload Commercial Conditions"),
        }

    def _get_reload_line_changes(self, line, condition):
        """Compare a sale order line with the condition and return changes.

        Order follows the pricing flow:
        base_price → ref. price → seller/extra disc. → unit price
        """
        changes = []

        def fmt(val):
            return formatLang(self.env, val, digits=2)

        # Resolve expected values
        new_base_price = self._get_fresh_base_price(line, condition)
        cond_seller, cond_extra, _source = self._get_condition_discount_for_product(
            line.product_id
        )
        base = new_base_price if new_base_price else line.base_price
        tax_rate, freight_rate, admin_rate = get_policy_rates(self.env)
        result = calc_line_pricing(
            base,
            condition.contractual_return,
            cond_seller,
            cond_extra,
            condition.cash_discount,
            condition.fob_discount,
            line.product_uom_qty,
            tax_rate,
            freight_rate,
            admin_rate,
        )
        new_ref_price = result["reference_price"]
        new_price_unit = result["price_unit"]

        # 1. Base price
        if new_base_price and abs(line.base_price - new_base_price) > 0.01:
            changes.append(
                _("Base price %(old)s → %(new)s")
                % {"old": fmt(line.base_price), "new": fmt(new_base_price)}
            )
        # 2. Reference price
        if new_ref_price and abs(line.reference_price - new_ref_price) > 0.01:
            changes.append(
                _("Ref. price %(old)s → %(new)s")
                % {"old": fmt(line.reference_price), "new": fmt(new_ref_price)}
            )
        # 3. Seller discount
        if line.seller_discount != cond_seller:
            changes.append(
                _("Seller disc. %(old)s%% → %(new)s%%")
                % {"old": fmt(line.seller_discount), "new": fmt(cond_seller)}
            )
        # 4. Extra discount
        if line.extra_discount != cond_extra:
            changes.append(
                _("Extra disc. %(old)s%% → %(new)s%%")
                % {"old": fmt(line.extra_discount), "new": fmt(cond_extra)}
            )
        # 5. Unit price (final)
        if new_price_unit and abs(line.price_unit - new_price_unit) > 0.01:
            changes.append(
                _("Unit price %(old)s → %(new)s")
                % {"old": fmt(line.price_unit), "new": fmt(new_price_unit)}
            )
        return changes

    def _get_fresh_base_price(self, line, condition):
        """Get base price from the condition's pricelist for comparison."""
        pricelist = condition.pricelist_id or self.pricelist_id
        if not pricelist or not line.product_id:
            return 0.0
        product = line.product_id
        price = pricelist._get_product_price(
            product,
            line.product_uom_qty or 1.0,
            uom=line.product_uom,
            date=self.date_order,
        )
        return product._get_tax_included_unit_price(
            line.company_id,
            self.currency_id,
            self.date_order,
            "sale",
            fiscal_position=self.fiscal_position_id,
            product_price_unit=price,
            product_currency=pricelist.currency_id,
        )

    def _apply_reload_conditions(self):
        """Apply reload — called by the wizard after user confirmation."""
        self.ensure_one()
        condition = self.commercial_condition_id
        if not condition:
            raise UserError(_("Cannot reload: customer has no commercial condition."))
        # 1. Pricelist from condition
        if condition.pricelist_id:
            self.pricelist_id = condition.pricelist_id
        # 2. Discounts and contractual return from condition
        self.cash_discount = condition.cash_discount
        self.fob_discount = condition.fob_discount
        self.contractual_return = condition.contractual_return
        self.discount_rate = (self.cash_discount or 0) + (self.fob_discount or 0)
        # 3. Recompute base_price for all lines (triggers reference_price cascade)
        product_lines = self.order_line.filtered(lambda line: line.product_id)
        product_lines.with_context(force_policy_recompute=True)._compute_base_price()
        # 4. Reapply discounts from condition to each line
        for line in product_lines:
            self._apply_condition_to_line(line, condition)
        # 5. Recompute price_unit and discount from policy
        tax_rate, freight_rate, admin_rate = get_policy_rates(self.env)
        for line in product_lines:
            if line.qty_invoiced > 0:
                continue
            result = calc_line_pricing(
                line.base_price,
                self.contractual_return,
                line.seller_discount,
                line.extra_discount,
                self.cash_discount,
                self.fob_discount,
                line.product_uom_qty,
                tax_rate,
                freight_rate,
                admin_rate,
            )
            line.with_context(tr_skip_price_protection=True).price_unit = result[
                "price_unit"
            ]
            line.with_context(tr_skip_price_protection=True).discount = result[
                "discount"
            ]
            line.discount_value = result["discount_value"]
        # 6. Re-resolve managed commissions on agent lines
        product_lines._resolve_agent_commissions()

    def action_recalculate_commissions(self):
        """Recalculate commission rates and resolve agent commissions.

        Uses the current seller_discount on each line to recompute the
        commission_rate from bands, then re-resolves the managed
        commission on agent lines. Does NOT change discounts or prices.
        """
        self.ensure_one()
        product_lines = self.order_line.filtered(lambda sol: sol.product_id)
        product_lines._compute_commission_rate()
        product_lines._resolve_agent_commissions()

    def _apply_condition_to_line(self, line, condition):
        """Apply commercial condition discounts to a sale order line."""
        if not condition or not line.product_id:
            return
        seller, extra, _level = condition._resolve_discount_for_product(line.product_id)
        line.seller_discount = seller
        line.extra_discount = extra
        line.discount_fixed = True

    @api.depends(
        "cash_discount",
        "fob_discount",
        "sales_profile_id",
        "sales_profile_id.cash_discount_max",
        "sales_profile_id.fob_discount_max",
        "sales_profile_id.manager_extra_limit",
        "sales_profile_id.cash_term_avg_days_max",
        "order_line.extra_discount",
        "order_line.seller_discount",
        "payment_term_avg_days",
        "amount_untaxed",
    )
    def _compute_discount_approval_level(self):
        for order in self:
            issues = order._get_discount_validation_issues()
            if any(issue["level"] == "director" for issue in issues):
                order.discount_approval_level = "director"
            elif any(issue["level"] == "manager" for issue in issues):
                order.discount_approval_level = "manager"
            else:
                order.discount_approval_level = "none"
            order.discount_approval_snapshot = (
                order._build_approval_snapshot(issues) if issues else False
            )

    def _get_discount_validation_issues(self):
        """Collect all discount policy violations for this order.

        Returns a list of dicts with keys: type, level, message, line_id.
        Used by the approval level compute, snapshot builder, and tests.
        """
        self.ensure_one()
        issues = []
        profile = self.sales_profile_id
        if not profile:
            return issues

        def fmt(val):
            return formatLang(self.env, val, digits=2)

        # Cash discount above limit
        if check_cash_discount_limit(self.cash_discount, profile.cash_discount_max):
            issues.append(
                {
                    "type": "cash_discount_limit",
                    "level": "director",
                    "message": _(
                        "Cash discount (%(disc)s%%) exceeds the maximum"
                        " (%(max)s%%) of profile '%(profile)s'."
                    )
                    % {
                        "disc": fmt(self.cash_discount),
                        "max": fmt(profile.cash_discount_max),
                        "profile": profile.name,
                    },
                    "line_id": False,
                }
            )
        # FOB discount above limit
        if check_fob_discount_limit(self.fob_discount, profile.fob_discount_max):
            issues.append(
                {
                    "type": "fob_discount_limit",
                    "level": "director",
                    "message": _(
                        "FOB discount (%(disc)s%%) exceeds the maximum"
                        " (%(max)s%%) of profile '%(profile)s'."
                    )
                    % {
                        "disc": fmt(self.fob_discount),
                        "max": fmt(profile.fob_discount_max),
                        "profile": profile.name,
                    },
                    "line_id": False,
                }
            )
        # Payment term above limit (with cash > 0)
        if check_payment_term_limit(
            self.cash_discount,
            self.payment_term_avg_days,
            profile.cash_term_avg_days_max,
        ):
            issues.append(
                {
                    "type": "payment_term_limit",
                    "level": "director",
                    "message": _(
                        "Payment term average (%(days)s days) exceeds the"
                        " maximum (%(max)s days) of profile '%(profile)s'."
                    )
                    % {
                        "days": fmt(self.payment_term_avg_days),
                        "max": fmt(profile.cash_term_avg_days_max),
                        "profile": profile.name,
                    },
                    "line_id": False,
                }
            )
        # Extra discount on lines
        manager_limit = profile.manager_extra_limit or 0
        for line in self.order_line.filtered(
            lambda sol: (sol.extra_discount or 0) > 0 and sol.product_id
        ):
            level = get_extra_discount_approval_level(
                line.extra_discount, manager_limit
            )
            if level == "director":
                issues.append(
                    {
                        "type": "extra_discount_director",
                        "level": "director",
                        "message": _(
                            "Line '%(product)s': extra discount (%(disc)s%%)"
                            " exceeds the manager limit (%(max)s%%)."
                        )
                        % {
                            "product": line.product_id.display_name,
                            "disc": fmt(line.extra_discount),
                            "max": fmt(manager_limit),
                        },
                        "line_id": line.id,
                    }
                )
            elif level == "manager":
                issues.append(
                    {
                        "type": "extra_discount",
                        "level": "manager",
                        "message": _(
                            "Line '%(product)s': extra discount of %(disc)s%%."
                        )
                        % {
                            "product": line.product_id.display_name,
                            "disc": fmt(line.extra_discount),
                        },
                        "line_id": line.id,
                    }
                )
        # Internal band violation
        if profile.profile_type == "internal":
            order_amount = self.amount_untaxed or 0.0
            for line in self.order_line.filtered(
                lambda sol: sol.seller_discount > 0 and sol.product_id
            ):
                rule = line._get_applicable_rule()
                if not rule or not rule.order_value_band_ids:
                    continue
                required_band = False
                for band in rule.order_value_band_ids.sorted("order_min_amount"):
                    if band.seller_discount_max >= line.seller_discount:
                        required_band = band
                        break
                if not required_band:
                    continue
                if order_amount < required_band.order_min_amount:
                    issues.append(
                        {
                            "type": "internal_band",
                            "level": "director",
                            "message": _(
                                "Line '%(product)s': order amount"
                                " (R$ %(amount).2f) is below the minimum"
                                " (R$ %(min).2f) required for discount"
                                " (%(disc).2f%%)."
                            )
                            % {
                                "product": line.product_id.display_name,
                                "amount": order_amount,
                                "min": required_band.order_min_amount,
                                "disc": line.seller_discount,
                            },
                            "line_id": line.id,
                        }
                    )
        return issues

    def _build_approval_snapshot(self, issues):
        """Build a human-readable snapshot from validation issues."""
        if not issues:
            return False
        return "\n".join(issue["message"] for issue in issues)

    def _notify_accepted_reviews_body(self):
        """Include discount violations in the approval chatter message."""
        base_body = super()._notify_accepted_reviews_body()
        snapshot = self.discount_approval_snapshot
        if not snapshot:
            return base_body
        violations = snapshot.replace("\n", "<br/>")
        return _(
            "%(base)s<br/><br/>"
            "<strong>Approved with the following policy exceptions:</strong>"
            "<br/>%(violations)s"
        ) % {"base": base_body, "violations": violations}

    def _sync_discount_validation_state(self):
        """Restart validation if needed.

        Called when discount fields change on orders that already have
        tier reviews. The snapshot is auto-computed, so only restart
        is needed here.
        """
        for order in self:
            if order.review_ids:
                order.restart_validation()

    @api.model
    def _get_under_validation_exceptions(self):
        """Allow editing discount fields while validation is pending."""
        res = super()._get_under_validation_exceptions()
        res.extend(
            [
                "cash_discount",
                "fob_discount",
                "commercial_condition_id",
                "payment_term_id",
                "order_line",
            ]
        )
        return res

    def write(self, vals):
        res = super().write(vals)
        revalidation_fields = {
            "cash_discount",
            "fob_discount",
            "commercial_condition_id",
            "payment_term_id",
        }
        if revalidation_fields.intersection(vals):
            for order in self.filtered("review_ids"):
                order._sync_discount_validation_state()
        return res

    def _is_commercial_policy_applicable(self):
        """Check if commercial policy validation should run for this order.

        Returns True only when the company has a default sales profile
        configured.  This is the single flag that indicates the
        commercial policy is active for this company.
        """
        self.ensure_one()
        return bool(self.company_id.default_sales_profile_id)

    def action_confirm(self):
        self._warn_pricelist_commission_module()
        for order in self:
            if not order._is_commercial_policy_applicable():
                continue
            order.order_line._validate_seller_discount_limit()
            order._check_sales_profile_required()
            order._check_commercial_condition_required()
            order._check_pricelist_matches_profile()
            order._check_orphan_commissions()
            order._check_stale_commissions()
            order._check_profile_consistency()
        return super().action_confirm()

    def _warn_pricelist_commission_module(self):
        """Block confirmation when sale_commission_pricelist is installed.

        Commission rates are managed by the commercial policy bands.
        The legacy pricelist commission module must be uninstalled
        to avoid ambiguity in commission resolution.
        """
        module = (
            self.env["ir.module.module"]
            .sudo()
            .search(
                [
                    ("name", "=", "sale_commission_pricelist"),
                    ("state", "=", "installed"),
                ],
                limit=1,
            )
        )
        if module:
            raise UserError(
                _(
                    "The module 'sale_commission_pricelist' is still"
                    " installed. Commission rates are managed by the"
                    " commercial policy bands. Please uninstall the"
                    " legacy module before confirming orders."
                )
            )

    def _check_pricelist_matches_profile(self):
        """Ensure order pricelist is in the profile's allowed pricelists."""
        self.ensure_one()
        profile = self.sales_profile_id
        if not profile or not profile.pricelist_ids:
            return
        if self.pricelist_id not in profile.pricelist_ids:
            raise ValidationError(
                _(
                    "The order pricelist '%(order_pl)s' is not allowed by"
                    " the sales profile '%(profile)s'. Allowed pricelists:"
                    " %(allowed)s.",
                    order_pl=self.pricelist_id.display_name,
                    profile=profile.display_name,
                    allowed=", ".join(profile.pricelist_ids.mapped("display_name")),
                )
            )

    def _check_sales_profile_required(self):
        """Ensure the order has a sales profile before confirmation.

        If the customer has agents but the agent has no profile,
        gives a specific error instead of falling back to user/team.
        """
        self.ensure_one()
        if not self.sales_profile_id:
            agents = self.partner_id.agent_ids if self.partner_id else False
            if agents:
                raise UserError(  # noqa: UP031
                    _(
                        "The customer '%(partner)s' has a sales agent "
                        "'%(agent)s' but the agent has no sales profile "
                        "assigned. Please set a profile on the agent."
                    )
                    % {
                        "partner": self.partner_id.display_name,
                        "agent": agents[0].display_name,
                    }
                )
            raise UserError(
                _(
                    "A sales profile is required to confirm this order. "
                    "Please set a profile on the salesperson or sales team."
                )
            )

    def _check_commercial_condition_required(self):
        """Ensure the order has a commercial condition before confirmation."""
        self.ensure_one()
        if not self.commercial_condition_id:
            raise UserError(
                _(
                    "A commercial condition is required to confirm this "
                    "order. Please set a commercial condition on the "
                    "customer '%(partner)s'."
                )
                % {"partner": self.partner_id.display_name}
            )

    def _check_stale_commissions(self):
        """Block confirmation when agent commissions are stale.

        When the order uses an agent profile with bands, verifies that
        each agent line's commission matches the current band rate.
        If stale, instructs the user to reload commercial conditions.
        """
        self.ensure_one()
        profile = self.sales_profile_id
        if not profile or profile.profile_type != "agent":
            return
        from odoo.tools import float_compare

        precision = self.env["decimal.precision"].precision_get("Discount Policy")
        stale_lines = []
        for line in self.order_line.filtered(lambda sol: sol.product_id):
            band_rate = line._get_commission_rate_from_bands()
            if band_rate is False:
                continue
            for agent_line in line.agent_ids:
                commission = agent_line.commission_id
                if not commission.tr_managed:
                    is_director = self.env.user.has_group(
                        "tr_commercial_policy.group_sales_director"
                    )
                    if not is_director:
                        stale_lines.append(
                            _(
                                "- '%(product)s': commission '%(comm)s' is"
                                " not managed by the commercial policy."
                                " Use 'Recalculate Commissions' to fix,"
                                " or ask a director to confirm."
                            )
                            % {
                                "product": line.product_id.display_name,
                                "comm": commission.display_name,
                            }
                        )
                    continue
                if (
                    float_compare(
                        commission.tr_rate,
                        band_rate,
                        precision_digits=precision,
                    )
                    != 0
                ):
                    stale_lines.append(
                        _(
                            "- '%(product)s': expected %(expected).2f%%,"
                            " has %(actual).2f%%"
                        )
                        % {
                            "product": line.product_id.display_name,
                            "expected": line.commission_rate,
                            "actual": commission.tr_rate,
                        }
                    )
        if stale_lines:
            raise UserError(
                _(
                    "Some lines have stale commission rates:\n"
                    "%(details)s\n\n"
                    "Please use 'Reload Commercial Conditions' to update."
                )
                % {"details": "\n".join(stale_lines)}
            )

    def _check_orphan_commissions(self):
        """Block orders with agent commissions when customer has no agents.

        If the customer does not have registered agents but order lines
        contain agent commission records, something is wrong (manual
        addition or code bug).
        """
        self.ensure_one()
        if self.partner_id.agent_ids:
            return
        lines_with_agents = self.order_line.filtered(lambda line: line.agent_ids)
        if lines_with_agents:
            raise UserError(
                _(
                    "Order lines have agent commissions but the customer "
                    "'%(partner)s' has no agents registered. Please remove "
                    "the commissions or register the agent on the customer."
                )
                % {"partner": self.partner_id.display_name}
            )

    def _check_profile_consistency(self):
        """Ensure the order's profile is consistent with its context.

        Agent profile: all line agents must have the same profile.
        Internal profile: no agent commissions + salesperson/team/company
        context must be compatible with the condition's profile.
        """
        self.ensure_one()
        profile = self.sales_profile_id
        if not profile:
            return
        if profile.profile_type == "agent":
            self._check_agent_line_profiles(profile)
        else:
            self._check_no_agents_on_internal(profile)
            self._check_internal_context_compatibility(profile)

    def _check_agent_line_profiles(self, profile):
        """Validate that all line agents have the same profile as the order."""
        for line in self.order_line.filtered(
            lambda sol: sol.product_id and sol.agent_ids
        ):
            for agent_line in line.agent_ids:
                agent_profile = agent_line.agent_id.sales_profile_id
                if agent_profile and agent_profile != profile:
                    raise UserError(  # noqa: UP031
                        _(
                            "Line '%(product)s' has agent '%(agent)s' with"
                            " profile '%(agent_profile)s', but the order uses"
                            " profile '%(order_profile)s'. All agents must"
                            " use the same profile."
                        )
                        % {
                            "product": line.product_id.display_name,
                            "agent": agent_line.agent_id.display_name,
                            "agent_profile": agent_profile.name,
                            "order_profile": profile.name,
                        }
                    )

    def _check_no_agents_on_internal(self, profile):
        """Block agent commissions on orders with internal profile."""
        lines_with_agents = self.order_line.filtered(
            lambda sol: sol.product_id and sol.agent_ids
        )
        if lines_with_agents:
            first_line = lines_with_agents[0]
            raise UserError(  # noqa: UP031
                _(
                    "The order uses internal profile '%(profile)s' but"
                    " line '%(product)s' has agent commissions."
                    " Internal orders should not have agent commissions."
                )
                % {
                    "profile": profile.name,
                    "product": first_line.product_id.display_name,
                }
            )

    def _resolve_compatibility_profile(self):
        """Resolve the expected profile for compatibility check.

        Mirrors ``partner_commercial_condition._resolve_applicable_profile_and_source``
        but in the order's perspective: salesperson → salesperson's team
        → order's team → company default. Each branch only "wins" if the
        candidate profile belongs to the order's company. Cross-company
        candidates are skipped, the chain continues — this matches the
        same semantics applied to condition resolution and prevents the
        check from falsely failing in multi-company setups.

        Returns ``(expected_profile, source_label)`` or ``(empty, "")``
        when no valid profile is resolvable.
        """
        self.ensure_one()
        company = self.company_id or self.env.company
        empty = self.env["tr.sales.profile"]
        # 1. Salesperson
        if self.user_id:
            profile = self.user_id.partner_id.with_company(company).sales_profile_id
            if profile and profile.company_id == company:
                return profile, _("salesperson '%s'") % self.user_id.name
            # 2. Salesperson's team
            if self.user_id.sale_team_id:
                profile = self.user_id.sale_team_id.sales_profile_id
                if profile and profile.company_id == company:
                    return profile, _("sales team '%s' (salesperson's team)") % (
                        self.user_id.sale_team_id.name
                    )
        # 3. Order's team
        if self.team_id:
            profile = self.team_id.sales_profile_id
            if profile and profile.company_id == company:
                return profile, _("sales team '%s'") % self.team_id.name
        # 4. Company default
        default = company.default_sales_profile_id
        if default and default.company_id == company:
            return default, _("company default")
        return empty, ""

    def _check_internal_context_compatibility(self, profile):
        """Verify internal profile matches the salesperson/team context.

        The order's profile comes from the condition, but the salesperson
        handling the order should be aligned. Uses ``_resolve_compatibility_profile``
        which traverses the chain skipping cross-company candidates.
        When no valid expected profile is found in the order's company
        (e.g. salesperson has profile only in another company and there
        is no team/default for this one), the check is a no-op — without
        an expected reference, comparison would be meaningless.
        """
        expected, source = self._resolve_compatibility_profile()
        if expected and expected != profile:
            raise UserError(  # noqa: UP031
                _(
                    "The order uses profile '%(order_profile)s'"
                    " (from commercial condition) but the"
                    " %(source)s has profile '%(expected)s'."
                )
                % {
                    "order_profile": profile.name,
                    "source": source,
                    "expected": expected.name,
                }
            )

    def action_open_save_condition_wizard(self):
        """Open the wizard to save order discounts to the customer condition."""
        self.ensure_one()
        condition = self.commercial_condition_id
        # Resolve current general values
        cash_current = condition.cash_discount if condition else 0.0
        fob_current = condition.fob_discount if condition else 0.0
        seller_current = condition.seller_discount if condition else 0.0

        wizard = self.env["tr.save.condition.wizard"].create(
            {
                "order_id": self.id,
                "cash_discount_current": cash_current,
                "cash_discount_new": self.cash_discount,
                "update_cash_discount": self.cash_discount != cash_current,
                "fob_discount_current": fob_current,
                "fob_discount_new": self.fob_discount,
                "update_fob_discount": self.fob_discount != fob_current,
                "seller_discount_current": seller_current,
                "seller_discount_new": self._get_general_seller_discount(),
                "update_seller_discount": False,
            }
        )
        # Build lines for products with different discounts
        for line in self.order_line.filtered(lambda sol: sol.product_id):
            (
                current_seller,
                current_extra,
                source,
            ) = self._get_condition_discount_for_product(line.product_id)
            if (
                line.seller_discount != current_seller
                or line.extra_discount != current_extra
            ):
                default_save_as = "variant" if source == "variant" else "template"
                self.env["tr.save.condition.wizard.line"].create(
                    {
                        "wizard_id": wizard.id,
                        "sale_line_id": line.id,
                        "product_id": line.product_id.id,
                        "product_tmpl_id": line.product_id.product_tmpl_id.id,
                        "seller_discount_current": current_seller,
                        "seller_discount_new": line.seller_discount,
                        "extra_discount_current": current_extra,
                        "extra_discount_new": line.extra_discount,
                        "save_as": default_save_as,
                    }
                )
        return {
            "type": "ir.actions.act_window",
            "res_model": "tr.save.condition.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
            "name": "Save Conditions to Customer",
        }

    def action_view_commercial_condition(self):
        """Open the customer's commercial condition form."""
        self.ensure_one()
        condition = self.commercial_condition_id
        if not condition:
            raise UserError(_("This customer has no commercial condition configured."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "partner.commercial.condition",
            "res_id": condition.id,
            "view_mode": "form",
            "target": "new",
            "name": _("Commercial Condition"),
        }

    def _get_general_seller_discount(self):
        """Get the representative general seller discount from order lines.

        If all lines have the same seller_discount, return it.
        Otherwise return the current condition's general seller_discount.
        """
        self.ensure_one()
        lines = self.order_line.filtered(lambda sol: sol.product_id)
        if not lines:
            return 0.0
        discounts = lines.mapped("seller_discount")
        if len(set(discounts)) == 1:
            return discounts[0]
        condition = self.commercial_condition_id
        return condition.seller_discount if condition else 0.0

    def _get_condition_discount_for_product(self, product):
        """Resolve current condition discount for a product.

        Returns (seller_discount, extra_discount, source_level).
        Delegates to condition._resolve_discount_for_product.
        """
        self.ensure_one()
        condition = self.commercial_condition_id
        if not condition:
            return 0.0, 0.0, "general"
        return condition._resolve_discount_for_product(product)

    def _get_invoice_grouping_keys(self):
        """Add commercial policy fields to invoice grouping keys.

        Ensures orders with different commercial context produce
        separate invoices.  Key names match invoice_vals fields
        (output of _prepare_invoice), not sale.order fields.
        """
        res = super()._get_invoice_grouping_keys()
        res.extend(
            [
                "commercial_condition_id",
                "sales_profile_id",
                "tr_cash_discount",
                "tr_fob_discount",
                "tr_contractual_return",
                "invoice_payment_term_id",
            ]
        )
        return res

    def _prepare_invoice(self):
        vals = super()._prepare_invoice()
        vals.update(
            {
                "tr_cash_discount": self.cash_discount,
                "tr_fob_discount": self.fob_discount,
                "tr_contractual_return": self.contractual_return,
                "commercial_condition_id": self.commercial_condition_id.id,
                "sales_profile_id": self.sales_profile_id.id,
            }
        )
        return vals

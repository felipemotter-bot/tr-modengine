# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import timedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare
from odoo.tools.misc import formatLang

from .policy_utils import (
    calc_adjustment_factor,
    calc_price_unit,
    calc_reference_price,
    check_cash_discount_limit,
    check_fob_discount_limit,
    check_payment_term_limit,
    get_extra_discount_approval_level,
    get_policy_rates,
)


class AccountMove(models.Model):
    _inherit = "account.move"

    tr_cash_discount = fields.Float(
        string="Cash Discount (%)",
    )
    tr_fob_discount = fields.Float(
        string="FOB Discount (%)",
    )
    tr_contractual_return = fields.Float(
        string="Contractual Return (%)",
        readonly=True,
    )
    has_sale_origin = fields.Boolean(
        compute="_compute_has_sale_origin",
    )
    unmanaged_commission_warning = fields.Char(
        compute="_compute_unmanaged_commission_warning",
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
    commercial_condition_id = fields.Many2one(
        comodel_name="partner.commercial.condition",
        string="Commercial Condition",
    )
    sales_profile_id = fields.Many2one(
        comodel_name="tr.sales.profile",
        string="Sales Profile",
    )
    payment_term_avg_days = fields.Float(
        compute="_compute_payment_term_avg_days",
    )

    # --- Display fields for invoice usability ---

    invoice_discount_pct = fields.Float(
        string="Invoice Discount (%)",
        compute="_compute_invoice_discount_pct",
        help="Total invoice discount: cash + FOB.",
    )
    adjustment_factor_display = fields.Char(
        string="Adjustment Factor",
        compute="_compute_adjustment_factor_display",
    )
    effective_pricelist_id = fields.Many2one(
        comodel_name="product.pricelist",
        string="Pricelist",
        compute="_compute_effective_pricelist_id",
    )
    invoice_divergence_warning = fields.Text(
        compute="_compute_invoice_divergence_warning",
        help=(
            "Non-stored live feedback. When this invoice originates"
            " from a sale order and the user edits something that"
            " breaks snapshot parity, the banner lists the divergences"
            " so the user can correct them BEFORE the"
            " post-time ``_check_invoice_policy`` hard block."
        ),
    )

    # Best-effort informational banner. ``@api.depends`` is intentionally
    # narrow — the compute reads ~10 fields across move, lines, origin
    # orders and company, and mirroring that full set would add a lot of
    # edges to a recurring Odoo 16 / l10n_br cold-start pain point
    # without proportional UX gain.
    #
    # Caveats the framework does NOT silently absorb:
    #
    # - ``@api.depends("invoice_line_ids")`` reacts to the relation
    #   changing (add / remove / relink) but NOT to writes on line
    #   fields such as ``seller_discount`` or ``price_unit``;
    # - changes on the origin ``sale.order`` (state, discounts,
    #   payment term), on ``company.invoice_validity_days`` or on
    #   ``invoice_date`` do not invalidate this cache either.
    #
    # The banner may therefore render stale between saves. Refreshing
    # the form (save, reopen) forces a recompute. ``action_post`` is
    # the source of truth — a stale banner never turns into a false
    # acceptance at post time.
    @api.depends("state", "invoice_line_ids")
    def _compute_invoice_divergence_warning(self):
        """Surface snapshot divergences while the invoice is still editable.

        Mirrors the short-circuit funnel of ``_check_invoice_policy`` so
        the banner never promises a block that ``action_post`` would not
        actually enforce:

        - only runs for draft ``out_invoice`` moves (refunds skip the
          commercial policy check, so no banner)
        - only runs when the policy is applicable to the company
        - a heterogeneous sale-origin invoice surfaces the structural
          block that ``_check_invoice_policy`` raises at post
        - invoices whose origin orders are not confirmed — or that are
          past the validity window — enter the ``own rules`` path at
          post, which is not driven by snapshot parity: silence the
          banner there
        - otherwise, collect snapshot issues and describe them
        """
        for move in self:
            if move.state != "draft":
                move.invoice_divergence_warning = False
                continue
            if move.move_type != "out_invoice":
                move.invoice_divergence_warning = False
                continue
            if not move.has_sale_origin:
                move.invoice_divergence_warning = False
                continue
            if not move._is_commercial_policy_applicable():
                move.invoice_divergence_warning = False
                continue
            if move._has_heterogeneous_sale_origins():
                move.invoice_divergence_warning = _(
                    "This invoice groups orders with different commercial"
                    " conditions. Please invoice each order separately."
                )
                continue
            if not move._sale_orders_confirmed() or not move._is_within_validity():
                # Own-rules / tier path at post — snapshot comparison
                # does not apply, and showing divergences here would
                # suggest a block that will never fire.
                move.invoice_divergence_warning = False
                continue
            # ``_get_invoice_snapshot_issues`` is also used by
            # ``_check_invoice_policy`` (post-time hard block) and may
            # raise ``UserError`` on structural problems such as an
            # invoice line mapped to more than one sale order line.
            # Swallow it here so opening / editing the form never
            # crashes — the hard block still fires at post.
            try:
                issues = move._get_invoice_snapshot_issues()
            except UserError as exc:
                move.invoice_divergence_warning = str(exc)
                continue
            if not issues:
                move.invoice_divergence_warning = False
                continue
            move.invoice_divergence_warning = move._build_core_divergence_message(
                issues
            )

    @api.depends("tr_cash_discount", "tr_fob_discount")
    def _compute_invoice_discount_pct(self):
        for move in self:
            move.invoice_discount_pct = (move.tr_cash_discount or 0) + (
                move.tr_fob_discount or 0
            )

    @api.depends("tr_contractual_return")
    def _compute_adjustment_factor_display(self):
        for move in self:
            cr = move.tr_contractual_return or 0
            if not cr:
                move.adjustment_factor_display = ""
                continue
            tax_rate, freight_rate, admin_rate = get_policy_rates(self.env)
            factor = calc_adjustment_factor(cr, tax_rate, freight_rate, admin_rate)
            move.adjustment_factor_display = "(+%.2f%% sobre preço base)" % factor

    @api.depends("commercial_condition_id", "has_sale_origin")
    def _compute_effective_pricelist_id(self):
        for move in self:
            if move.move_type not in ("out_invoice", "out_refund"):
                move.effective_pricelist_id = False
                continue
            move.effective_pricelist_id = move._get_effective_pricelist()

    # --- Extend invoice usability tree hooks ---

    def _get_invoice_line_tree_keep_fields(self):
        return super()._get_invoice_line_tree_keep_fields() | {
            "base_price",
            "reference_price",
            "seller_discount",
            "extra_discount",
            "commission_rate",
        }

    def _get_invoice_line_tree_field_order(self):
        return [
            "product_id",
            "name",
            "quantity",
            "base_price",
            "reference_price",
            "seller_discount",
            "extra_discount",
            "price_unit",
            "fiscal_operation_id",
            "fiscal_operation_line_id",
            "cfop_id",
            "fiscal_tax_ids",
            "account_id",
            "commission_rate",
            "price_gross",
            "price_subtotal",
        ]

    def _get_invoice_line_default_optional_hide(self):
        return super()._get_invoice_line_default_optional_hide() | {
            "base_price",
            "reference_price",
            "seller_discount",
            "extra_discount",
            "commission_rate",
        }

    @api.depends(
        "invoice_line_ids.sale_line_ids",
        "invoice_line_ids.display_type",
    )
    def _compute_has_sale_origin(self):
        for move in self:
            move.has_sale_origin = any(
                line.sale_line_ids
                for line in move.invoice_line_ids.filtered(
                    lambda line: line.display_type == "product"
                )
            )

    # Best-effort informational banner — same semantics and caveats
    # as ``sale.order._compute_unmanaged_commission_warning``:
    # ``@api.depends("invoice_line_ids")`` reacts to the relation,
    # NOT to writes on line-level fields
    # (``agent_ids.commission_id``, etc.). Banner may go stale
    # between saves; a reload or save refreshes it.
    @api.depends("state", "invoice_line_ids")
    def _compute_unmanaged_commission_warning(self):
        for move in self:
            if move.move_type != "out_invoice":
                move.unmanaged_commission_warning = False
                continue
            if not move.has_sale_origin:
                move.unmanaged_commission_warning = False
                continue
            unmanaged_products = []
            for inv_line in move.invoice_line_ids.filtered(
                lambda line: line.sale_line_ids and line.display_type == "product"
            ):
                # Skip unsaved lines for the same reason as on sale.order:
                # agent/commission records are only reconciled after the
                # line exists; evaluating them as ``NewId`` produces a
                # false-positive warning that disappears after the first
                # save.
                if not isinstance(inv_line.id, int):
                    continue
                for agent_line in inv_line.agent_ids:
                    if not agent_line.commission_id.tr_managed:
                        unmanaged_products.append(inv_line.product_id.display_name)
                        break
            if unmanaged_products:
                move.unmanaged_commission_warning = _(
                    "Some lines have commissions not managed by the"
                    " commercial policy: %s."
                    " Review them before posting."
                ) % ", ".join(unmanaged_products)
            else:
                move.unmanaged_commission_warning = False

    # --- Manual invoice: condition snapshot via onchange ---

    @api.onchange("partner_id")
    def _onchange_partner_commercial_condition(self):
        """Prefill commercial condition from partner (manual invoice)."""
        if self.has_sale_origin:
            return
        if self.partner_id:
            self.commercial_condition_id = (
                self.partner_id.effective_condition_id or False
            )
        else:
            self.commercial_condition_id = False
        self._apply_manual_invoice_condition_snapshot()

    @api.onchange("commercial_condition_id")
    def _onchange_commercial_condition(self):
        """Apply condition snapshot when condition changes."""
        self._apply_manual_invoice_condition_snapshot()

    @api.onchange("tr_cash_discount", "tr_fob_discount")
    def _onchange_cash_fob_discount(self):
        """Recalculate line discount when cash/fob changes.

        Same behavior as sale.order._onchange_cash_fob_discount.
        """
        new_discount = (self.tr_cash_discount or 0.0) + (self.tr_fob_discount or 0.0)
        for line in self._get_policy_invoice_lines():
            line.discount = new_discount

    def _is_manual_policy_invoice(self):
        """Check if this is a manual draft sales invoice under policy."""
        self.ensure_one()
        return (
            self.move_type == "out_invoice"
            and self.state == "draft"
            and not self.has_sale_origin
        )

    def _apply_manual_invoice_condition_snapshot(self):
        """Apply commercial condition as snapshot to the manual invoice.

        NewId records use update() in cache.
        Persistent records use account.move.write(line_ids=[Command.update(...)])
        so dynamic lines remain synchronized by the standard account flow.
        """
        if self.has_sale_origin:
            return
        condition = self.commercial_condition_id
        header_vals = self._prepare_condition_header_vals(condition)

        if isinstance(self.id, models.NewId):  # pragma: no cover — NewId/onchange only
            self.update(header_vals)  # pragma: no cover
            if condition:  # pragma: no cover
                for line in self._get_policy_invoice_lines():  # pragma: no cover
                    self._apply_condition_to_invoice_line(
                        line, condition
                    )  # pragma: no cover
            else:  # pragma: no cover
                self._clear_manual_invoice_policy_lines()  # pragma: no cover
            return  # pragma: no cover

        manual_lines = self._get_policy_invoice_lines().filtered(
            lambda line: not line.sale_line_ids
        )
        line_commands = []
        for line in manual_lines:
            line_vals = (
                self._prepare_condition_line_vals(line, condition)
                if condition
                else self._prepare_clear_line_vals()
            )
            line_commands.append(Command.update(line.id, line_vals))

        write_vals = dict(header_vals)
        if line_commands:
            write_vals["line_ids"] = line_commands
        self.with_context(
            tr_skip_price_protection=True,
            tr_skip_manual_snapshot=True,
        ).write(write_vals)

        if condition:
            self._resolve_manual_invoice_agent_commissions()
        else:
            for line in manual_lines:
                line.agent_ids = [(5, 0, 0)]

    def _prepare_condition_header_vals(self, condition):
        """Return header snapshot values from the current condition."""
        if condition:
            return {
                "sales_profile_id": condition.applicable_profile_id.id,
                "tr_cash_discount": condition.cash_discount,
                "tr_fob_discount": condition.fob_discount,
                "tr_contractual_return": condition.contractual_return,
                "invoice_payment_term_id": (
                    condition.payment_term_id.id if condition.payment_term_id else False
                ),
            }
        return {
            "sales_profile_id": False,
            "tr_cash_discount": 0.0,
            "tr_fob_discount": 0.0,
            "tr_contractual_return": 0.0,
            "invoice_payment_term_id": False,
        }

    def _prepare_condition_line_vals(self, line, condition):
        """Return the full pricing-chain values for one invoice line.

        Uses condition values directly (not self.tr_*) to avoid
        stale header values when called before move.write().
        """
        self.ensure_one()
        if not condition or not line.product_id:
            return {}
        (
            seller_discount,
            extra_discount,
            _source,
        ) = condition._resolve_discount_for_product(line.product_id)
        base_price = line._compute_manual_base_price()
        tax_rate, freight_rate, admin_rate = get_policy_rates(self.env)
        reference_price = calc_reference_price(
            base_price,
            condition.contractual_return,
            tax_rate,
            freight_rate,
            admin_rate,
        )
        price_unit = calc_price_unit(
            reference_price,
            seller_discount,
            extra_discount,
        )
        discount = (condition.cash_discount or 0.0) + (condition.fob_discount or 0.0)
        commission_rate = (
            line._get_commission_rate_for_discount(
                seller_discount,
                profile=condition.applicable_profile_id,
            )
            or 0.0
        )
        return {
            "seller_discount": seller_discount,
            "extra_discount": extra_discount,
            "extra_discount_reason": False,
            "base_price": base_price,
            "reference_price": reference_price,
            "price_unit": price_unit,
            "discount": discount,
            "commission_rate": commission_rate,
        }

    def _prepare_clear_line_vals(self):
        """Return values that clear manual policy state from a line."""
        return {
            "seller_discount": 0.0,
            "extra_discount": 0.0,
            "extra_discount_reason": False,
            "base_price": 0.0,
            "reference_price": 0.0,
            "commission_rate": 0.0,
            "discount": 0.0,
            "price_unit": 0.0,
        }

    def _clear_manual_invoice_policy_lines(self):  # pragma: no cover — NewId only
        """Clear policy fields on manual lines (NewId/onchange only)."""
        manual_lines = self._get_policy_invoice_lines().filtered(  # pragma: no cover
            lambda line: not line.sale_line_ids
        )
        clear_vals = self._prepare_clear_line_vals()  # pragma: no cover
        for line in manual_lines:  # pragma: no cover
            line.update(clear_vals)  # pragma: no cover
            line.agent_ids = [(5, 0, 0)]  # pragma: no cover

    def _apply_condition_to_invoice_line(self, line, condition):
        """Apply pricing chain to one line (NewId/onchange only)."""
        line_vals = self._prepare_condition_line_vals(line, condition)
        if (
            not line_vals
        ):  # pragma: no cover — condition+product always present at call site
            return  # pragma: no cover
        line.update(line_vals)

    # --- Manual invoice: agent commissions ---

    def _recompute_manual_invoice_agents(self):
        """Kept for NewId/onchange path. For persistent records, agent
        rederivation happens via recompute_lines_agents() in backfill.
        """
        pass

    def _resolve_manual_invoice_agent_commissions(self):
        """Adjust agent commission_id to managed commission from bands.

        Agents are derived by the standard OCA/Engenere flow (via
        onchange or create).  This method only adjusts commission_id
        on existing agents — it does NOT add or remove agents.
        """
        Commission = self.env["commission"]
        for line in self._get_policy_invoice_lines():
            profile = self.sales_profile_id
            if not profile or profile.profile_type != "agent":
                continue
            band_rate = line._get_commission_rate_for_discount(line.seller_discount)
            if band_rate is False:
                continue
            for agent_line in line.agent_ids:
                base_commission = agent_line.agent_id.commission_id
                if (
                    not base_commission
                ):  # pragma: no cover — agent partner requires commission
                    continue  # pragma: no cover
                commission = Commission._ensure_managed_commission(
                    base_commission.invoice_state,
                    band_rate,
                )
                if commission != agent_line.commission_id:
                    agent_line.commission_id = commission

    # --- Manual invoice: prerequisites ---

    def _check_manual_invoice_policy_prerequisites(self):
        """Block posting when structural policy base is missing."""
        self.ensure_one()
        if not self.partner_id:
            raise UserError(
                _("A customer is required to post a manual" " sales invoice.")
            )
        if not self.commercial_condition_id:
            raise UserError(
                _("A commercial condition is required for customer '%s'.")
                % self.partner_id.display_name
            )
        if not self.sales_profile_id:
            raise UserError(
                _(
                    "A sales profile is required. Review the"
                    " commercial condition or partner setup."
                )
            )

    def _check_mixed_invoice_lines(self):
        """Block invoices that mix sale-origin and manual product lines."""
        self.ensure_one()
        if not self.has_sale_origin:
            return
        manual_lines = self.invoice_line_ids.filtered(
            lambda line: (
                line.display_type == "product"
                and line.product_id
                and not line.sale_line_ids
            )
        )
        if manual_lines:
            raise UserError(
                _(
                    "This invoice mixes sale-origin and manual lines."
                    " Please use separate invoices."
                )
            )

    # --- View customization ---

    def _reorganize_invoice_line_form(self, arch):
        """Hide the raw ``price_unit`` where the rich block takes over.

        ``trento_invoice_usability`` moves a plain ``<field
        name="price_unit"/>`` into the ``invoice_line_left`` anchor as
        part of its generic 2-column layout. This module injects a
        richer block (``reference_price → price_unit`` + calculator)
        into the same anchor via XML, wrapped in a div that is
        invisible for non-sales moves. On sales invoices the rich
        block renders, so we hide the plain field to avoid a
        duplicate "Preço Unitário" row. On vendor bills the rich
        block stays hidden, so we leave the plain field visible —
        otherwise the user has no way to edit the price.
        """
        result = super()._reorganize_invoice_line_form(arch)
        for left in arch.xpath(
            "//field[@name='invoice_line_ids']/form"
            "//group[@name='invoice_line_left']"
        ):
            for node in left.xpath("./field[@name='price_unit']"):
                node.set(
                    "attrs",
                    "{'invisible': [('parent.move_type', 'in',"
                    " ['out_invoice', 'out_refund'])]}",
                )
        return result

    # --- Decision tree (principle 4) ---

    def action_post(self):
        """Override to apply commercial policy validation on out_invoice."""
        for move in self.filtered(lambda move: move.move_type == "out_invoice"):
            move._warn_invoice_br_discount_module()
            move._check_invoice_policy()
        return super().action_post()

    def _warn_invoice_br_discount_module(self):
        """Block posting when the legacy br invoice discount module is
        still installed.

        The commercial policy manages invoice discounts via
        ``seller_discount`` + ``extra_discount``. The legacy
        ``engenere_account_invoice_br_discount`` module adds a
        ``discount_fixed`` field that is no longer used — it should be
        uninstalled to avoid confusion.

        Mirrors ``sale.order._warn_pricelist_commission_module``.
        Only fires when the commercial policy is active for the company,
        to avoid blocking unrelated modules that happen to have the
        legacy module installed.
        """
        self.ensure_one()
        if not self._is_commercial_policy_applicable():
            return
        module = (
            self.env["ir.module.module"]
            .sudo()
            .search(
                [
                    ("name", "=", "engenere_account_invoice_br_discount"),
                    ("state", "=", "installed"),
                ],
                limit=1,
            )
        )
        if module:
            raise UserError(
                _(
                    "The module 'engenere_account_invoice_br_discount' is"
                    " still installed. Invoice discounts are managed by the"
                    " commercial policy via seller/extra discount. Please"
                    " uninstall the legacy module before posting invoices."
                )
            )

    def _is_commercial_policy_applicable(self):
        """Check if commercial policy validation should run.

        Returns True only when the company has a default sales profile
        configured.  This is the single flag that indicates the
        commercial policy is active for this company.

        This guard prevents the commercial policy from interfering with
        tests and CI of modules that have nothing to do with commercial
        conditions (e.g. tr_cogs_report creating plain invoices).
        """
        self.ensure_one()
        return bool(self.company_id.default_sales_profile_id)

    def _check_invoice_policy(self):
        """Decision tree for invoice commercial validation.

        Manual invoice: prerequisites + own rules + tier.
        Sale-origin invoice: snapshot parity for B/C (hard block),
        own rules + tier for A divergences / draft / qty excess.

        Skips entirely when no sales profile is configured, so
        unrelated modules are not affected by commercial policy.
        """
        self.ensure_one()
        if not self._is_commercial_policy_applicable():
            return
        if not self.has_sale_origin:
            self._check_manual_invoice_policy_prerequisites()
            self._resolve_manual_invoice_agent_commissions()
            self._validate_invoice_own_rules()
            if self.need_validation:
                raise UserError(
                    _(
                        "This invoice requires approval before posting."
                        " Request validation from the approval panel."
                    )
                )
            return
        self._check_mixed_invoice_lines()
        if self._has_heterogeneous_sale_origins():
            raise UserError(
                _(
                    "This invoice groups orders with different commercial"
                    " conditions. Please invoice each order separately."
                )
            )
        # 1. Confirmed? 2. Within validity?
        # Both bypass snapshot and enter own-rules directly.
        if not self._sale_orders_confirmed() or not self._is_within_validity():
            self._apply_own_rules_and_check_tier()
            return
        # 3. Snapshot checks (only for confirmed, valid invoices)
        hard_block, own_rule = self._split_invoice_divergence_issues()
        if hard_block:
            raise UserError(self._build_core_divergence_message(hard_block))
        if self._qty_exceeds_order() or own_rule:
            self._apply_own_rules_and_check_tier()
            return
        # PARITY — confirmed + valid + no issues → post free

    def _get_sale_origin_lines(self):
        """Return product invoice lines that originate from a sale order."""
        return self.invoice_line_ids.filtered(
            lambda line: line.sale_line_ids and line.display_type == "product"
        )

    def _get_policy_invoice_lines(self):
        """Return all product invoice lines subject to commercial policy.

        This is the official boundary for Phase 2+: all policy validation,
        constraints, and prefill operate on this set — regardless of whether
        lines originate from a sale order or are manually created.
        """
        return self.invoice_line_ids.filtered(
            lambda line: line.display_type == "product" and line.product_id
        )

    def _get_effective_sales_profile(self):
        """Return the effective sales profile for this invoice.

        Sale-origin: profile from the origin sale order.
        Manual: profile snapshot stored on the invoice.
        """
        self.ensure_one()
        if self.has_sale_origin:
            return self._get_origin_sales_profile()
        return self.sales_profile_id

    def _get_effective_pricelist(self):
        """Return the effective pricelist for price resolution.

        Sale-origin: pricelist from the origin sale order.
        Manual: pricelist from the commercial condition.
        """
        self.ensure_one()
        if self.has_sale_origin:
            orders = self._get_origin_sale_orders()
            return orders[:1].pricelist_id if orders else False
        condition = self.commercial_condition_id
        return condition.pricelist_id if condition else False

    def _get_origin_sale_orders(self):
        """Return sale orders that originated this invoice."""
        return self._get_sale_origin_lines().sale_line_ids.mapped("order_id")

    def _has_heterogeneous_sale_origins(self):
        """Check if origin orders have different commercial contexts.

        Compares commercial_condition_id, sales_profile_id,
        payment_term_id, and discount values (with float_compare).
        Returns True if any order differs from the first.
        """
        orders = self._get_origin_sale_orders()
        if len(orders) <= 1:
            return False
        precision = self.env["decimal.precision"].precision_get("Discount Policy")
        first = orders[0]
        return any(
            order.commercial_condition_id != first.commercial_condition_id
            or order.sales_profile_id != first.sales_profile_id
            or order.payment_term_id != first.payment_term_id
            or float_compare(
                order.cash_discount,
                first.cash_discount,
                precision_digits=precision,
            )
            != 0
            or float_compare(
                order.fob_discount,
                first.fob_discount,
                precision_digits=precision,
            )
            != 0
            or float_compare(
                order.contractual_return,
                first.contractual_return,
                precision_digits=precision,
            )
            != 0
            for order in orders[1:]
        )

    def _sale_orders_confirmed(self):
        """Check all origin sale orders are in confirmed state."""
        orders = self._get_origin_sale_orders()
        return all(order.state in ("sale", "done") for order in orders)

    def _qty_exceeds_order(self):
        """Check if accumulated invoiced qty exceeds the ordered quantity."""
        for inv_line in self._get_sale_origin_lines():
            for sale_line in inv_line.sale_line_ids:
                # qty_invoiced includes all posted + current draft invoices
                # for this sale line. Compare accumulated total vs ordered.
                if (
                    float_compare(
                        sale_line.qty_invoiced,
                        sale_line.product_uom_qty,
                        precision_rounding=sale_line.product_uom.rounding,
                    )
                    > 0
                ):
                    return True
        return False

    def _is_within_validity(self):
        """Check if the invoice is within the configured validity period.

        Compares the invoice effective date against the oldest origin
        order's date_order plus the company's validity days.
        Returns True if within deadline or feature disabled (days=0).
        """
        self.ensure_one()
        days = self.company_id.invoice_validity_days or 0
        if not days:
            return True
        orders = self._get_origin_sale_orders()
        if not orders:
            return True
        # date_order = confirmation date when state='sale' in Odoo 16
        oldest_date = min(orders.mapped("date_order"))
        deadline = fields.Date.to_date(oldest_date) + timedelta(days=days)
        effective_date = (
            self.invoice_date or self.date or fields.Date.context_today(self)
        )
        return effective_date <= deadline

    def _get_expected_agent_snapshot(self, inv_line):
        """Expected agent snapshot from sale line(s): {(agent_id, commission_id)}."""
        expected = set()
        for sale_line in inv_line.sale_line_ids:
            for agent in sale_line.agent_ids:
                expected.add((agent.agent_id.id, agent.commission_id.id))
        return expected

    def _get_actual_agent_snapshot(self, inv_line):
        """Actual agent snapshot on invoice line: {(agent_id, commission_id)}."""
        return {
            (agent.agent_id.id, agent.commission_id.id) for agent in inv_line.agent_ids
        }

    def _build_core_divergence_message(self, issues):
        """Build user-friendly message depending on divergence type.

        Issues are dicts with 'kind' and 'message'.  If any issue is
        commission/agent related, the hint points to the Recalculate
        button.
        """
        commission_related = any(
            issue["kind"] in ("commission", "agent") for issue in issues
        )
        if commission_related:
            hint = _(
                "Use 'Resync From Sale Order' to restore inherited"
                " values, or adjust the invoice to match the order."
            )
        else:
            hint = _(
                "Adjust the invoice to match the sale order,"
                " or regenerate it from the sale order if needed."
            )
        details = "\n".join(issue["message"] for issue in issues)
        return _("Invoice diverges from the sale order:\n%(details)s\n\n%(hint)s") % {
            "details": details,
            "hint": hint,
        }

    def _get_invoice_snapshot_issues(self):
        """Collect all divergence issues between invoice and sale order.

        Returns list of dicts with 'kind' and 'message'.  Checks both
        field-level snapshot and agent snapshot in a single pass.
        """
        self.ensure_one()
        issues = []
        precision = self.env["decimal.precision"].precision_get("Discount Policy")
        price_precision = self.env["decimal.precision"].precision_get("Sale Price")
        # Header divergence
        orders = self._get_origin_sale_orders()
        if orders:
            order = orders[0]
            if (
                float_compare(
                    self.tr_cash_discount,
                    order.cash_discount,
                    precision_digits=precision,
                )
                != 0
            ):
                issues.append(
                    {
                        "kind": "cash_discount",
                        "message": _(
                            "Cash discount: invoice %(inv).2f%% vs order %(sale).2f%%"
                        )
                        % {
                            "inv": self.tr_cash_discount,
                            "sale": order.cash_discount,
                        },
                    }
                )
            if (
                float_compare(
                    self.tr_fob_discount,
                    order.fob_discount,
                    precision_digits=precision,
                )
                != 0
            ):
                issues.append(
                    {
                        "kind": "fob_discount",
                        "message": _(
                            "FOB discount: invoice %(inv).2f%% vs order %(sale).2f%%"
                        )
                        % {
                            "inv": self.tr_fob_discount,
                            "sale": order.fob_discount,
                        },
                    }
                )
            if (
                float_compare(
                    self.tr_contractual_return,
                    order.contractual_return,
                    precision_digits=precision,
                )
                != 0
            ):
                issues.append(
                    {
                        "kind": "contractual_return",
                        "message": _(
                            "Contractual return: invoice %(inv).2f%%"
                            " vs order %(sale).2f%%"
                        )
                        % {
                            "inv": self.tr_contractual_return,
                            "sale": order.contractual_return,
                        },
                    }
                )
            if self.invoice_payment_term_id != order.payment_term_id:
                issues.append(
                    {
                        "kind": "payment_term",
                        "message": _("Payment term diverges from the sale order."),
                    }
                )
        # Line-level divergence
        for inv_line in self._get_sale_origin_lines():
            product_name = inv_line.product_id.display_name
            if len(inv_line.sale_line_ids) > 1:
                raise UserError(
                    _(
                        "Line '%(product)s' has %(count)d sale order lines."
                        " The commercial policy currently supports only"
                        " one-to-one invoice-to-sale line mapping."
                    )
                    % {
                        "product": product_name,
                        "count": len(inv_line.sale_line_ids),
                    }
                )
            sale_line = inv_line.sale_line_ids[:1]
            if not sale_line:  # pragma: no cover — _get_sale_origin_lines pre-filters
                continue  # pragma: no cover
            if (
                float_compare(
                    inv_line.seller_discount,
                    sale_line.seller_discount,
                    precision_digits=precision,
                )
                != 0
            ):
                issues.append(
                    {
                        "kind": "seller_discount",
                        "message": _(
                            "Line '%s': seller discount diverges from the sale order."
                        )
                        % product_name,
                    }
                )
            if (
                float_compare(
                    inv_line.extra_discount,
                    sale_line.extra_discount,
                    precision_digits=precision,
                )
                != 0
            ):
                issues.append(
                    {
                        "kind": "extra_discount",
                        "message": _(
                            "Line '%s': extra discount diverges" " from the sale order."
                        )
                        % product_name,
                    }
                )
            if (inv_line.extra_discount_reason or False) != (
                sale_line.extra_discount_reason or False
            ):
                issues.append(
                    {
                        "kind": "extra_discount_reason",
                        "message": _(
                            "Line '%s': extra discount reason diverges"
                            " from the sale order."
                        )
                        % product_name,
                    }
                )
            if (
                float_compare(
                    inv_line.base_price,
                    sale_line.base_price,
                    precision_digits=price_precision,
                )
                != 0
                or float_compare(
                    inv_line.reference_price,
                    sale_line.reference_price,
                    precision_digits=price_precision,
                )
                != 0
            ):
                issues.append(
                    {
                        "kind": "base_price",
                        "message": _("Line '%s': price diverges from the sale order.")
                        % product_name,
                    }
                )
            if (
                float_compare(
                    inv_line.commission_rate,
                    sale_line.commission_rate,
                    precision_digits=precision,
                )
                != 0
            ):
                issues.append(
                    {
                        "kind": "commission_rate",
                        "message": _(
                            "Line '%s': commission rate diverges from the sale order."
                        )
                        % product_name,
                    }
                )
            # price_unit vs expected from policy formula (only when
            # reference_price is set — otherwise not a policy-managed price)
            if inv_line.reference_price:
                expected_price = calc_price_unit(
                    inv_line.reference_price,
                    inv_line.seller_discount,
                    inv_line.extra_discount,
                )
                if (
                    float_compare(
                        inv_line.price_unit,
                        expected_price,
                        precision_digits=price_precision,
                    )
                    != 0
                ):
                    issues.append(
                        {
                            "kind": "price_unit",
                            "message": _(
                                "Line '%s': unit price diverges from the"
                                " expected policy value."
                            )
                            % product_name,
                        }
                    )
            # Note: native Odoo `discount` is derived from cash_discount +
            # fob_discount. Those are checked at header level. Comparing
            # discount directly produces false positives because other
            # modules (l10n_br, punctuality) may modify it independently.
            # Phase 5 will add direct price/discount governance.
            if len(inv_line.agent_ids) > 1:
                issues.append(
                    {
                        "kind": "agent",
                        "message": _("Line '%s': more than one agent is not allowed.")
                        % product_name,
                    }
                )
            expected = self._get_expected_agent_snapshot(inv_line)
            actual = self._get_actual_agent_snapshot(inv_line)
            if expected != actual:
                missing = expected - actual
                extra = actual - expected
                if missing:
                    issues.append(
                        {
                            "kind": "commission",
                            "message": _(
                                "Line '%s': agent commission diverges"
                                " from the sale order."
                            )
                            % product_name,
                        }
                    )
                if extra:
                    issues.append(
                        {
                            "kind": "agent",
                            "message": _(
                                "Line '%s': has agents not present in the sale order."
                            )
                            % product_name,
                        }
                    )
        return issues

    # --- Taxonomia / divergence classification ---

    # Classe A: inputs comerciais editáveis → own rules
    OWN_RULE_KINDS = {
        "seller_discount",
        "extra_discount",
        "extra_discount_reason",
        "cash_discount",
        "fob_discount",
        "payment_term",
    }

    def _split_invoice_divergence_issues(self):
        """Split snapshot issues into hard-block vs own-rule.

        Returns (hard_block, own_rule) — two lists of issue dicts.
        Classe A → own_rule, Classe B + C → hard_block.
        """
        self.ensure_one()
        hard_block = []
        own_rule = []
        for issue in self._get_invoice_snapshot_issues():
            if issue["kind"] in self.OWN_RULE_KINDS:
                own_rule.append(issue)
            else:
                hard_block.append(issue)
        return hard_block, own_rule

    # --- Profile resolution ---

    def _get_origin_sales_profile(self):
        """Return the sales profile from the origin sale order(s).

        Accepts multi-order when all orders share the same profile
        (homogeneous context, guaranteed by grouping keys).
        Raises UserError for heterogeneous origins.
        """
        self.ensure_one()
        orders = self._get_origin_sale_orders()
        if not orders:
            raise UserError(
                _("Commercial revalidation requires at least one" " sale-order origin.")
            )
        if self._has_heterogeneous_sale_origins():
            raise UserError(
                _("This invoice groups orders with different" " commercial conditions.")
            )
        return orders[0].sales_profile_id

    # --- Payment term ---

    @api.depends("invoice_payment_term_id")
    def _compute_payment_term_avg_days(self):
        """Calculate weighted average payment term days.

        Same formula as sale.order._compute_payment_term_avg_days.
        """
        for move in self:
            term = move.invoice_payment_term_id
            if not term or not term.line_ids:
                move.payment_term_avg_days = 0.0
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
            move.payment_term_avg_days = (
                total_days / total_weight if total_weight else 0.0
            )

    # --- Discount approval level (tier) ---

    @api.depends(
        "move_type",
        "has_sale_origin",
        "partner_id",
        "sales_profile_id",
        "tr_cash_discount",
        "tr_fob_discount",
        "invoice_payment_term_id",
        "invoice_line_ids.seller_discount",
        "invoice_line_ids.extra_discount",
        "invoice_line_ids.product_id",
        "invoice_line_ids.sale_line_ids",
    )
    def _compute_discount_approval_level(self):
        """Compute approval level from the invoice's own values.

        Depends only on invoice fields — the invoice is a snapshot of
        the moment it was created.  Divergence from the origin sale
        order is checked at action_post time, not here.
        """
        for move in self:
            if move.move_type != "out_invoice":
                move.discount_approval_level = "none"
                move.discount_approval_snapshot = False
                continue
            # Without prerequisites, no tier (error is structural, not tier)
            if not move.partner_id or not move.sales_profile_id:
                move.discount_approval_level = "none"
                move.discount_approval_snapshot = False
                continue
            if move.has_sale_origin and move._has_heterogeneous_sale_origins():
                move.discount_approval_level = "none"
                move.discount_approval_snapshot = False
                continue
            issues = move._get_invoice_discount_validation_issues()
            if any(issue["level"] == "director" for issue in issues):
                move.discount_approval_level = "director"
            elif any(issue["level"] == "manager" for issue in issues):
                move.discount_approval_level = "manager"
            else:
                move.discount_approval_level = "none"
            move.discount_approval_snapshot = (
                move._build_approval_snapshot(issues) if issues else False
            )

    def _get_invoice_discount_validation_issues(self):
        """Collect discount policy violations for this invoice.

        Evaluates the current invoice values against the commercial
        rules from the effective sales profile.  Returns list of dicts
        with keys: type, level, message, line_id.

        Internal band validation is excluded (Phase 2/3).
        """
        self.ensure_one()
        issues = []
        try:
            profile = self._get_effective_sales_profile()
        except UserError:
            return issues
        if not profile:
            return issues

        def fmt(val):
            return formatLang(self.env, val, digits=2)

        if check_cash_discount_limit(self.tr_cash_discount, profile.cash_discount_max):
            issues.append(
                {
                    "type": "cash_discount_limit",
                    "level": "director",
                    "message": _(
                        "Cash discount (%(disc)s%%) exceeds the maximum"
                        " (%(max)s%%) of profile '%(profile)s'."
                    )
                    % {
                        "disc": fmt(self.tr_cash_discount),
                        "max": fmt(profile.cash_discount_max),
                        "profile": profile.name,
                    },
                    "line_id": False,
                }
            )
        if check_fob_discount_limit(self.tr_fob_discount, profile.fob_discount_max):
            issues.append(
                {
                    "type": "fob_discount_limit",
                    "level": "director",
                    "message": _(
                        "FOB discount (%(disc)s%%) exceeds the maximum"
                        " (%(max)s%%) of profile '%(profile)s'."
                    )
                    % {
                        "disc": fmt(self.tr_fob_discount),
                        "max": fmt(profile.fob_discount_max),
                        "profile": profile.name,
                    },
                    "line_id": False,
                }
            )
        if check_payment_term_limit(
            self.tr_cash_discount,
            self.payment_term_avg_days,
            profile.cash_term_avg_days_max,
        ):
            issues.append(
                {
                    "type": "payment_term_limit",
                    "level": "director",
                    "message": _(
                        "Payment term average (%(days)s days) exceeds"
                        " the maximum (%(max)s days) of profile"
                        " '%(profile)s'."
                    )
                    % {
                        "days": fmt(self.payment_term_avg_days),
                        "max": fmt(profile.cash_term_avg_days_max),
                        "profile": profile.name,
                    },
                    "line_id": False,
                }
            )
        manager_limit = profile.manager_extra_limit or 0
        for inv_line in self._get_policy_invoice_lines():
            if not inv_line.extra_discount or inv_line.extra_discount <= 0:
                continue
            level = get_extra_discount_approval_level(
                inv_line.extra_discount, manager_limit
            )
            if level == "director":
                issues.append(
                    {
                        "type": "extra_discount_director",
                        "level": "director",
                        "message": _(
                            "Line '%(product)s': extra discount"
                            " (%(disc)s%%) exceeds the manager"
                            " limit (%(max)s%%)."
                        )
                        % {
                            "product": inv_line.product_id.display_name,
                            "disc": fmt(inv_line.extra_discount),
                            "max": fmt(manager_limit),
                        },
                        "line_id": inv_line.id,
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
                            "product": inv_line.product_id.display_name,
                            "disc": fmt(inv_line.extra_discount),
                        },
                        "line_id": inv_line.id,
                    }
                )
        return issues

    def _build_approval_snapshot(self, issues):
        """Build a human-readable snapshot from validation issues."""
        if not issues:
            return False
        return "\n".join(issue["message"] for issue in issues)

    # --- Own rules validation ---

    def _apply_own_rules_and_check_tier(self):
        """Run own-rules validation and check tier approval.

        Raises UserError if tier validation is pending.
        """
        self.ensure_one()
        self._validate_invoice_own_rules()
        if self.need_validation:
            raise UserError(
                _(
                    "This invoice requires approval before posting."
                    " Request validation from the approval panel."
                )
            )

    def _validate_invoice_own_rules(self):
        """Validate invoice-side commercial rules.

        Reached for:
        - Manual invoices (own rules always)
        - Sale-origin invoices that lost parity (divergence / draft / qty)
        """
        self.ensure_one()
        # Check direct price_unit edits on manual lines
        self._check_manual_price_integrity()
        # Note: native discount is NOT checked — other modules modify it
        # Hard constraints on all policy lines
        for inv_line in self._get_policy_invoice_lines():
            inv_line._validate_seller_discount_limit()
        # Tier is handled by mixin (need_validation blocks post button)
        # The backend guard in _check_invoice_policy checks need_validation

    def _check_manual_price_integrity(self):
        """Block posting if price_unit was directly edited on manual lines.

        Compares current price_unit against the expected value from the
        policy chain (calc_price_unit).  Any mismatch indicates direct
        editing, which is not allowed.

        Skips lines whose product category allows manual price edit
        (Phase 5 — price protection by category).
        """
        self.ensure_one()
        price_precision = self.env["decimal.precision"].precision_get("Sale Price")
        for line in self._get_policy_invoice_lines().filtered(
            lambda inv_l: not inv_l.sale_line_ids and inv_l.reference_price
        ):
            if line.product_id.categ_id.allow_manual_price_edit:
                continue
            expected = calc_price_unit(
                line.reference_price,
                line.seller_discount,
                line.extra_discount,
            )
            if (
                float_compare(
                    line.price_unit,
                    expected,
                    precision_digits=price_precision,
                )
                != 0
            ):
                raise UserError(
                    _(
                        "Line '%(product)s': unit price was directly"
                        " edited. Use seller/extra discount instead."
                    )
                    % {"product": line.product_id.display_name}
                )

    # --- Fiscal document import ---

    @api.model
    def import_fiscal_document(
        self, fiscal_document, move_id=None, move_type="in_invoice"
    ):
        """Add price protection bypass for fiscal document import.

        l10n_br_account writes price_unit directly on invoice lines
        during import.  This is an internal operation, not a manual
        user edit, so the write-time guard must be skipped.
        """
        return super(
            AccountMove,
            self.with_context(tr_skip_price_protection=True),
        ).import_fiscal_document(fiscal_document, move_id=move_id, move_type=move_type)

    # --- Tier integration ---

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        manual_moves = moves.filtered(
            lambda move: (
                move._is_manual_policy_invoice()
                and move._is_commercial_policy_applicable()
                and move.partner_id
                and not move.commercial_condition_id
            )
        )
        if manual_moves:
            for move in manual_moves:
                move.commercial_condition_id = (
                    move.partner_id.effective_condition_id or False
                )
            manual_moves.with_context(
                tr_skip_manual_snapshot=True
            )._apply_manual_condition_backfill()
        return moves

    def _apply_manual_condition_backfill(self):
        """Apply partner-derived condition snapshot after backend writes."""
        for move in self:
            move.with_context(
                tr_skip_manual_snapshot=True
            )._apply_manual_invoice_condition_snapshot()
        # Flush + rederive agents after all writes complete
        self.env.flush_all()
        self.env.invalidate_all()
        for move in self:
            if move.commercial_condition_id:
                move.recompute_lines_agents()
                move._resolve_manual_invoice_agent_commissions()

    @api.model
    def _get_under_validation_exceptions(self):
        """Allow editing discount fields while validation is pending."""
        res = super()._get_under_validation_exceptions()
        res.extend(
            [
                "tr_cash_discount",
                "tr_fob_discount",
                "invoice_payment_term_id",
                "invoice_line_ids",
            ]
        )
        return res

    def write(self, vals):
        result = super().write(vals)
        if self.env.context.get("tr_skip_manual_snapshot"):
            return result
        if "partner_id" in vals:
            manual_moves = self.filtered(
                lambda move: move._is_manual_policy_invoice()
                and move._is_commercial_policy_applicable()
            )
            for move in manual_moves:
                move.commercial_condition_id = (
                    move.partner_id.effective_condition_id or False
                )
            if manual_moves:
                manual_moves.with_context(
                    tr_skip_manual_snapshot=True
                )._apply_manual_condition_backfill()
        # cash/fob change recalculates line.discount on any draft out_invoice
        cash_fob_fields = {"tr_cash_discount", "tr_fob_discount"}
        if cash_fob_fields.intersection(vals):
            draft_invoices = self.filtered(
                lambda m: m.move_type == "out_invoice" and m.state == "draft"
            )
            for move in draft_invoices:
                new_discount = (move.tr_cash_discount or 0.0) + (
                    move.tr_fob_discount or 0.0
                )
                for line in move._get_policy_invoice_lines():
                    line.with_context(
                        check_move_validity=False,
                        tr_skip_price_protection=True,
                    ).write({"discount": new_discount})
        revalidation_fields = {
            "tr_cash_discount",
            "tr_fob_discount",
            "invoice_payment_term_id",
        }
        if revalidation_fields.intersection(vals):
            for move in self.filtered("review_ids"):
                move._sync_discount_validation_state()
        return result

    def _sync_discount_validation_state(self):
        """Restart tier validation when discount fields change."""
        for move in self:
            if move.review_ids:
                move.restart_validation()

    def _notify_accepted_reviews_body(self):
        """Include discount violations in the approval chatter message."""
        base_body = super()._notify_accepted_reviews_body()
        snapshot = self.discount_approval_snapshot
        if not snapshot:
            return base_body
        violations = snapshot.replace("\n", "<br/>")
        return _(
            "%(base)s<br/><br/>"
            "<strong>Approved with the following policy exceptions:"
            "</strong><br/>%(violations)s"
        ) % {"base": base_body, "violations": violations}

    # --- Resync button ---

    def action_resync_from_sale_order(self):
        """Restore the invoice snapshot inherited from the sale order.

        Syncs header fields (cash/fob/contractual), line-level fields
        (seller_discount, commission_rate, etc.) and agent lines.
        Agent sync uses three layers for settlement safety:
        1. Update commission_id on existing agents (safe)
        2. Create missing agents
        3. Remove extra agents only if not settled
        """
        self.ensure_one()
        if self._has_heterogeneous_sale_origins():
            raise UserError(
                _(
                    "This invoice groups orders with different commercial"
                    " conditions. Please invoice each order separately."
                )
            )
        # Resync header fields
        orders = self._get_origin_sale_orders()
        if orders:
            order = orders[0]
            self.with_context(check_move_validity=False).write(
                {
                    "tr_cash_discount": order.cash_discount,
                    "tr_fob_discount": order.fob_discount,
                    "tr_contractual_return": order.contractual_return,
                    "invoice_payment_term_id": order.payment_term_id.id,
                    "commercial_condition_id": order.commercial_condition_id.id,
                    "sales_profile_id": order.sales_profile_id.id,
                }
            )
        AgentModel = self.env["account.invoice.line.agent"]
        ctx = {"skip_invoice_sync": True, "check_move_validity": False}
        for inv_line in self._get_sale_origin_lines():
            if len(inv_line.sale_line_ids) > 1:
                raise UserError(
                    _(
                        "Line '%(product)s' has %(count)d sale order lines."
                        " The commercial policy currently supports only"
                        " one-to-one invoice-to-sale line mapping."
                    )
                    % {
                        "product": inv_line.product_id.display_name,
                        "count": len(inv_line.sale_line_ids),
                    }
                )
            sale_line = inv_line.sale_line_ids[:1]
            if not sale_line:  # pragma: no cover — _get_sale_origin_lines pre-filters
                continue  # pragma: no cover

            # Resync snapshot fields from sale line
            inv_line.with_context(**ctx).write(
                {
                    "seller_discount": sale_line.seller_discount,
                    "extra_discount": sale_line.extra_discount,
                    "extra_discount_reason": (sale_line.extra_discount_reason or False),
                    "base_price": sale_line.base_price,
                    "reference_price": sale_line.reference_price,
                    "commission_rate": sale_line.commission_rate,
                }
            )

            # Agent sync
            expected = self._get_expected_agent_snapshot(inv_line)
            actual = self._get_actual_agent_snapshot(inv_line)
            if expected == actual:
                continue

            # Pre-compute sale agent map to avoid N+1 queries
            sale_agents = inv_line.sale_line_ids.mapped("agent_ids")
            sale_agent_map = {}
            for sa in sale_agents:
                sale_agent_map[sa.agent_id.id] = sa
            expected_agent_ids = set(sale_agent_map.keys())

            # Layer 1: Update commission_id on existing agents
            for inv_agent in inv_line.agent_ids:
                sale_agent = sale_agent_map.get(inv_agent.agent_id.id)
                if sale_agent and sale_agent.commission_id != inv_agent.commission_id:
                    inv_agent.commission_id = sale_agent.commission_id

            # Layer 2: Add missing agents
            existing_agent_ids = set(inv_line.agent_ids.mapped("agent_id").ids)
            for agent_id, sale_agent in sale_agent_map.items():
                if agent_id not in existing_agent_ids:
                    AgentModel.create(
                        {
                            "object_id": inv_line.id,
                            "agent_id": agent_id,
                            "commission_id": sale_agent.commission_id.id,
                        }
                    )

            # Layer 3: Remove extra agents (only if not settled)
            for inv_agent in inv_line.agent_ids:
                if inv_agent.agent_id.id not in expected_agent_ids:
                    if not inv_agent.settled:
                        inv_agent.unlink()

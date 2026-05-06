# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from dateutil.relativedelta import relativedelta
from markupsafe import escape as html_escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools.misc import formatLang

from .policy_utils import (
    calc_adjustment_factor,
    get_policy_rates,
    validate_seller_markup,
)

_logger = logging.getLogger(__name__)


_LINE_TRACKED_FIELDS = (
    "applied_on",
    "product_tmpl_id",
    "product_id",
    "seller_discount",
    "extra_discount",
)
_BAND_TRACKED_FIELDS = (
    "qty_min",
    "qty_uom_id",
    "seller_discount",
    "extra_discount",
)


def _field_label(record, fname):
    """Translated field label for the user's language.

    ``fields_get`` returns the ``string`` resolved through
    ``ir.translation`` for ``env.lang`` (the request user's language).
    Reading ``record._fields[fname].string`` returns the source label
    (English in this codebase) regardless of language.
    """
    return record.fields_get([fname])[fname]["string"]


def _format_tracked_value(record, fname, value):
    """Formatter for the chatter tracking helpers.

    Only handles the field types present in ``_LINE_TRACKED_FIELDS`` /
    ``_BAND_TRACKED_FIELDS`` (float, many2one, selection). Float must be
    checked before any "empty" branch because ``0.0 == False`` in Python,
    which would render ``0.0`` as "(empty)" and lose the new value on a
    5% → 0% diff.
    """
    field = record._fields[fname]
    if field.type == "float":
        return formatLang(record.env, value or 0.0, digits=2)
    if field.type == "many2one":
        return value.display_name if value else _("(empty)")
    # selection
    if not value:
        return _("(empty)")
    return dict(field._description_selection(record.env)).get(value, str(value))


_CHATTER_SUPPRESS_CONTEXT_KEYS = (
    "tracking_disable",
    "mail_notrack",
    "mail_create_nolog",
)


def _chatter_suppressed(env):
    ctx = env.context
    return any(ctx.get(key) for key in _CHATTER_SUPPRESS_CONTEXT_KEYS)


def _render_changes_html(record, changes):
    rows = "".join(
        "<li><b>%s</b>: %s → %s</li>"
        % (html_escape(label), html_escape(old), html_escape(new))
        for (label, old, new) in changes
    )
    return "<ul>%s</ul>" % rows


def _render_values_html(record, values):
    rows = "".join(
        "<li><b>%s</b>: %s</li>" % (html_escape(label), html_escape(value))
        for (label, value) in values
    )
    return "<ul>%s</ul>" % rows


class PartnerCommercialCondition(models.Model):
    _name = "partner.commercial.condition"
    _description = "Partner Commercial Condition"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "display_name"
    # Odoo's auto check: any ``Many2one`` with ``check_company=True``
    # on this model must point at a record whose ``company_id`` is
    # either empty (company-less / shared) or matches ``self.company_id``.
    # Covers create, write, copy, and defaults. Needed to block the
    # cross-company references Felipe found in devel (e.g. a condition
    # of TRENTO pointing at a payment_mode_id of TREINAMENTO).
    _check_company_auto = True

    display_name = fields.Char(compute="_compute_display_name")

    @api.depends("partner_id", "partner_id.name")
    def _compute_display_name(self):
        for record in self:
            if record.partner_id:
                record.display_name = _("Condition of %s", record.partner_id.name)
            else:
                record.display_name = _("Commercial Condition")

    partner_id = fields.Many2one(
        comodel_name="res.partner",
        required=True,
        ondelete="cascade",
    )
    pricelist_id = fields.Many2one(
        comodel_name="product.pricelist",
        string="Pricelist",
        required=True,
        default=lambda self: self._default_pricelist_id(),
        tracking=True,
        check_company=True,
    )
    payment_term_id = fields.Many2one(
        comodel_name="account.payment.term",
        string="Payment Terms",
        tracking=True,
        check_company=True,
    )
    incoterm_id = fields.Many2one(
        comodel_name="account.incoterms",
        string="Incoterm",
        tracking=True,
    )
    payment_mode_id = fields.Many2one(
        comodel_name="account.payment.mode",
        string="Payment Mode",
        tracking=True,
        check_company=True,
    )
    delivery_carrier_id = fields.Many2one(
        comodel_name="delivery.carrier",
        string="Delivery Method",
        tracking=True,
        check_company=True,
    )

    @api.model
    def _resolve_default_pricelist_for_partner(self, partner, company=None):
        """Default pricelist for a given partner's applicable profile.

        ``company`` is forwarded to the profile resolver so cross-company
        creates pick the pricelist of the target company, not of
        ``env.company``.
        """
        company = company or self.env.company
        profile = self._get_applicable_profile(partner=partner, company=company)
        if profile and profile.pricelist_ids:
            return profile.pricelist_ids[0]
        return self.env["product.pricelist"].search(
            [("company_id", "in", (company.id, False))], limit=1
        )

    @api.model
    def _default_pricelist_id(self):
        """Default pricelist from the applicable sales profile."""
        return self._resolve_default_pricelist_for_partner(None)

    contractual_return = fields.Float(
        string="Contractual Return (%)",
        tracking=True,
        help="Contractual return percentage (formerly punctuality discount). "
        "Used to adjust reference price.",
    )
    adjustment_factor = fields.Float(
        string="Adjustment Factor (%)",
        compute="_compute_adjustment_factor",
        help="Price adjustment percentage due to contractual return.",
    )
    discount_display = fields.Selection(
        selection=[
            ("show_discounts", "Show Discounts"),
            ("net_price", "Net Price Only"),
        ],
        default="show_discounts",
        tracking=True,
        help="Controls how prices appear on printed documents. "
        "'Show Discounts' displays reference price with discount breakdown. "
        "'Net Price Only' shows only the final price.",
    )
    cash_discount = fields.Float(
        string="Cash Discount (%)",
        tracking=True,
    )
    fob_discount = fields.Float(
        string="FOB Discount (%)",
        tracking=True,
    )
    seller_discount = fields.Float(
        string="Seller Discount (%) - General",
        tracking=True,
        help="Default seller discount for products without a specific line.",
    )
    line_ids = fields.One2many(
        comodel_name="partner.commercial.condition.line",
        inverse_name="condition_id",
        string="Product Lines",
    )
    has_any_band = fields.Boolean(
        compute="_compute_has_any_band",
        help="True when at least one line has qty bands defined. Used "
        "by the lines tree to hide the Bands column when no line uses "
        "the feature.",
    )

    @api.depends("line_ids.band_ids")
    def _compute_has_any_band(self):
        for condition in self:
            condition.has_any_band = any(line.band_ids for line in condition.line_ids)

    company_id = fields.Many2one(
        comodel_name="res.company",
        default=lambda self: self.env.company,
    )
    applicable_profile_id = fields.Many2one(
        comodel_name="tr.sales.profile",
        compute="_compute_applicable_profile",
        store=True,
    )
    profile_source = fields.Selection(
        selection=[
            ("agent", "Sales Agent"),
            ("salesperson", "Partner's Salesperson"),
            ("salesperson_team", "Salesperson's Team"),
            ("partner_team", "Partner's Team"),
            ("company", "Company Default"),
        ],
        compute="_compute_applicable_profile",
        store=True,
        help="Indicates where the applicable sales profile was resolved from.",
    )

    @api.depends(
        "company_id",
        "company_id.default_sales_profile_id",
        "company_id.default_sales_profile_id.company_id",
        "partner_id",
        "partner_id.agent_ids",
        "partner_id.agent_ids.sales_profile_id",
        "partner_id.agent_ids.sales_profile_id.company_id",
        "partner_id.user_id",
        "partner_id.user_id.partner_id.sales_profile_id",
        "partner_id.user_id.partner_id.sales_profile_id.company_id",
        "partner_id.user_id.sale_team_id",
        "partner_id.user_id.sale_team_id.sales_profile_id",
        "partner_id.user_id.sale_team_id.sales_profile_id.company_id",
        "partner_id.team_id",
        "partner_id.team_id.sales_profile_id",
        "partner_id.team_id.sales_profile_id.company_id",
    )
    def _compute_applicable_profile(self):
        for condition in self:
            profile, source = condition._resolve_applicable_profile_and_source()
            condition.applicable_profile_id = profile
            condition.profile_source = source

    @api.depends("contractual_return")
    def _compute_adjustment_factor(self):
        tax_rate, freight_rate, admin_rate = get_policy_rates(self.env)
        for condition in self:
            condition.adjustment_factor = calc_adjustment_factor(
                condition.contractual_return, tax_rate, freight_rate, admin_rate
            )

    _sql_constraints = [
        (
            "partner_unique",
            "UNIQUE(partner_id, company_id)",
            "Only one commercial condition per partner per company is allowed.",
        ),
    ]

    # ---- Profile-based validation (Gap 2 / Gap 3) ----

    def _resolve_applicable_profile_and_source(self, partner=None, company=None):
        """Resolve sales profile and its source for the condition's partner.

        Resolution chain (first match wins, profile must belong to ``company``):
        1. Partner's agent → agent's profile in ``company``
        2. Partner's salesperson → salesperson's profile in ``company``
        3. Salesperson's team → team's profile (if it belongs to ``company``)
        4. Partner's team → team's profile (if it belongs to ``company``)
        5. Company default (if it belongs to ``company``)

        Returns ``(profile_recordset, source_string_or_False)``.
        ``profile_source`` is ``False`` when no valid (same-company) profile
        is resolved — keeps diagnostics honest instead of claiming a
        ``"company"`` source when the default is invalid.

        ``partner`` overrides ``self.partner_id`` (needed during ``create``
        when ``self`` is empty). ``company`` overrides ``self.company_id``
        (needed during ``create`` for the same reason — ``self.env.company``
        would resolve against the wrong company in cross-company creates).

        Each branch reads ``sales_profile_id`` (``company_dependent`` on
        ``res.partner``) via ``with_company(company)`` so the value comes
        from the right company's ``ir.property``. A defensive
        ``profile.company_id == company`` check guards against any path
        that could leak a profile from another company (e.g. teams whose
        ``sales_profile_id`` is a plain Many2one without
        ``company_dependent``).
        """
        partner = partner or (self.partner_id if self else False)
        company = company or (self.company_id if self else False) or self.env.company
        empty = self.env["tr.sales.profile"]
        # 1. Agent
        if partner and partner.agent_ids:
            profile = partner.agent_ids[0].with_company(company).sales_profile_id
            if profile and profile.company_id == company:
                return profile, "agent"
        # 2. Partner's salesperson
        if partner and partner.user_id:
            profile = partner.user_id.partner_id.with_company(company).sales_profile_id
            if profile and profile.company_id == company:
                return profile, "salesperson"
            # 3. Salesperson's team
            if partner.user_id.sale_team_id:
                profile = partner.user_id.sale_team_id.sales_profile_id
                if profile and profile.company_id == company:
                    return profile, "salesperson_team"
        # 4. Partner's team
        if partner and partner.team_id:
            profile = partner.team_id.sales_profile_id
            if profile and profile.company_id == company:
                return profile, "partner_team"
        # 5. Company default
        default = company.default_sales_profile_id
        if default and default.company_id == company:
            return default, "company"
        # Nothing resolved cleanly — return empty + False source to signal
        # "no valid profile" rather than pretending the company default won.
        return empty, False

    def _get_applicable_profile(self, partner=None, company=None):
        """Resolve sales profile for the condition's partner.

        Convenience wrapper around ``_resolve_applicable_profile_and_source``.
        ``company`` is propagated for cross-company creates where ``self``
        is empty.
        """
        profile, _source = self._resolve_applicable_profile_and_source(partner, company)
        return profile

    def _get_partner_avg_order_amount(self, partner, months=6, company=None):
        """Average amount_untaxed of confirmed orders in last N months.

        ``company`` defaults to ``self.company_id`` when called on a
        condition recordset (the most common path — discount validation
        from condition write/create), falling back to ``env.company``.
        Without an explicit company, the average leaks orders from
        ``env.company`` even when the calling condition belongs to a
        different company, producing the wrong band match in internal-
        profile discount limits.
        """
        if company is None:
            company = (self.company_id if self else False) or self.env.company
        date_from = fields.Date.today() - relativedelta(months=months)
        orders = self.env["sale.order"].search(
            [
                ("partner_id", "=", partner.id),
                ("state", "in", ("sale", "done")),
                ("date_order", ">=", date_from),
                ("company_id", "=", company.id),
            ]
        )
        if not orders:
            return 0.0
        return sum(orders.mapped("amount_untaxed")) / len(orders)

    def _validate_discount_limits(self, vals, partner=None, company=None):
        """Validate discount values against the partner's profile.

        Raises AccessError if no profile can be resolved.
        Raises ValidationError if discounts exceed profile limits.
        Directors bypass all validations.
        ``partner`` is needed during create when self is empty.
        ``company`` is needed during create for the same reason — without
        it the resolver would fall back to ``env.company`` and validate
        against the wrong company's profile.
        """
        if self.env.user.has_group("tr_commercial_policy.group_sales_director"):
            return

        # Only validate if discount fields are being written
        discount_fields = {
            "cash_discount",
            "fob_discount",
            "seller_discount",
        }
        if not discount_fields.intersection(vals):
            return

        profile = self._get_applicable_profile(partner=partner, company=company)
        if not profile:
            raise AccessError(
                _("You need a sales profile to edit commercial conditions.")
            )

        # Cash discount
        if "cash_discount" in vals:
            if vals["cash_discount"] > profile.cash_discount_max:
                raise ValidationError(  # noqa: UP031
                    _(
                        "Cash discount (%(disc).2f%%) exceeds the"
                        " maximum allowed (%(max).2f%%) by sales"
                        " profile '%(profile)s'."
                    )
                    % {
                        "disc": vals["cash_discount"],
                        "max": profile.cash_discount_max,
                        "profile": profile.name,
                    }
                )

        # FOB discount
        if "fob_discount" in vals:
            if vals["fob_discount"] > profile.fob_discount_max:
                raise ValidationError(  # noqa: UP031
                    _(
                        "FOB discount (%(disc).2f%%) exceeds the"
                        " maximum allowed (%(max).2f%%) by sales"
                        " profile '%(profile)s'."
                    )
                    % {
                        "disc": vals["fob_discount"],
                        "max": profile.fob_discount_max,
                        "profile": profile.name,
                    }
                )

        # Seller discount (general)
        if "seller_discount" in vals:
            self._validate_seller_discount(
                vals["seller_discount"], profile, partner=partner, company=company
            )

    def _validate_seller_discount(
        self, seller_discount, profile, partner=None, company=None
    ):
        """Validate general seller_discount against profile rules.

        For internal profiles, uses the 6-month average to find the band.
        ``partner`` is required when called during create (self is empty).
        ``company`` propagates to ``_get_partner_avg_order_amount`` so
        the average is computed against the right company's orders
        (defaults to the condition's company / env.company).
        """
        validate_seller_markup(self.env, seller_discount)
        general_rule = profile.rule_ids.filtered(
            lambda rule: rule.applied_on == "general"
        )
        if not general_rule:
            return
        general_rule = general_rule[0]

        if profile.profile_type == "internal" and general_rule.order_value_band_ids:
            partners = self.mapped("partner_id") if self else partner
            for part in partners:
                avg_amount = self._get_partner_avg_order_amount(part, company=company)
                bands = general_rule.order_value_band_ids.filtered(
                    lambda band, avg=avg_amount: band.order_min_amount <= avg
                ).sorted("order_min_amount")
                max_discount = bands[-1].seller_discount_max if bands else 0.0
                if seller_discount > max_discount:
                    raise ValidationError(  # noqa: UP031
                        _(
                            "Seller discount (%(disc).2f%%) exceeds"
                            " the maximum allowed (%(max).2f%%) for"
                            " the customer's average order amount"
                            " (R$ %(avg).2f)."
                        )
                        % {
                            "disc": seller_discount,
                            "max": max_discount,
                            "avg": avg_amount,
                        }
                    )
        else:
            if seller_discount > general_rule.seller_discount_max:
                raise ValidationError(  # noqa: UP031
                    _(
                        "Seller discount (%(disc).2f%%) exceeds the"
                        " maximum allowed (%(max).2f%%) by sales"
                        " profile '%(profile)s'."
                    )
                    % {
                        "disc": seller_discount,
                        "max": general_rule.seller_discount_max,
                        "profile": profile.name,
                    }
                )

    def _validate_pricelist(self, vals, partner=None, company=None):
        """Validate pricelist against the partner's profile.

        Directors bypass all validations.
        ``partner`` overrides ``self.partner_id`` (needed during create).
        ``company`` overrides ``self.company_id`` (needed during create
        to resolve the profile in the right company).
        """
        if self.env.user.has_group("tr_commercial_policy.group_sales_director"):
            return
        if "pricelist_id" not in vals:
            return
        profile = self._get_applicable_profile(partner=partner, company=company)
        if not profile or not profile.pricelist_ids:
            return
        if vals["pricelist_id"] not in profile.pricelist_ids.ids:
            raise ValidationError(
                _(
                    "The pricelist is not in the allowed pricelists of your"
                    " sales profile '%(profile)s'."
                )
                % {"profile": profile.name}
            )

    _SYNC_FIELDS = {
        "pricelist_id",
        "payment_term_id",
        "payment_mode_id",
        "incoterm_id",
        "delivery_carrier_id",
        "contractual_return",
    }

    @api.model_create_multi
    def create(self, vals_list):
        Partner = self.env["res.partner"]
        Company = self.env["res.company"]
        is_manager = self.env.user.has_group("tr_commercial_policy.group_sales_manager")
        is_su = self.env.su
        for vals in vals_list:
            partner = Partner.browse(vals.get("partner_id")).exists()
            # Resolve the target company explicitly so cross-company
            # creates (env.company is A, vals["company_id"] is B) validate
            # discounts/pricelist against the profile of company B, not
            # the silent fallback of env.company.
            company = (
                Company.browse(vals["company_id"])
                if vals.get("company_id")
                else self.env.company
            )
            if "pricelist_id" in vals and not is_su and not is_manager:
                expected = self._resolve_default_pricelist_for_partner(
                    partner, company=company
                )
                expected_id = expected.id if expected else False
                if vals["pricelist_id"] != expected_id:
                    raise AccessError(
                        _(
                            "Only sales managers can choose the pricelist on a "
                            "commercial condition. The default from the sales "
                            "profile is used automatically."
                        )
                    )
            self._validate_pricelist(vals, partner=partner, company=company)
            self._validate_discount_limits(vals, partner=partner, company=company)
        records = super().create(vals_list)
        records._sync_to_partners()
        return records

    def write(self, vals):
        if (
            "pricelist_id" in vals
            and not self.env.su
            and not self.env.user.has_group("tr_commercial_policy.group_sales_manager")
        ):
            raise AccessError(
                _(
                    "Only sales managers can change the pricelist on a "
                    "commercial condition."
                )
            )
        self._validate_pricelist(vals)
        self._validate_discount_limits(vals)
        result = super().write(vals)
        if self._SYNC_FIELDS.intersection(vals):
            self._sync_to_partners()
        return result

    def _resolve_discount_for_product(self, product, qty=0.0, uom=None):
        """Resolve discount for a product: variant > template > general.

        Pure resolution — no side effects on any record.
        Returns (seller_discount, extra_discount, source_level).

        ``qty`` is the quantity of the order/invoice line (NOT the
        order total). ``uom`` is the line's UoM; defaults to
        ``product.uom_id`` when omitted.

        Each line's effective discount is selected from its
        ``band_ids`` based on ``qty`` (highest ``qty_min`` ≤ qty
        wins, after UoM normalization). When no band fits or
        ``band_ids`` is empty, the line's direct
        ``seller_discount``/``extra_discount`` fields are used as
        the implicit qty_min=0 band.
        """
        self.ensure_one()
        line_uom = uom or product.uom_id
        # 1. Variant-specific line
        variant_line = self.line_ids.filtered(lambda cline: cline.product_id == product)
        if variant_line:
            seller, extra = variant_line[0]._resolve_discount_for_qty(qty, line_uom)
            return (seller, extra, "variant")
        # 2. Template-specific line
        tmpl_line = self.line_ids.filtered(
            lambda cline: cline.product_tmpl_id == product.product_tmpl_id
            and cline.applied_on == "product_template"
        )
        if tmpl_line:
            seller, extra = tmpl_line[0]._resolve_discount_for_qty(qty, line_uom)
            return (seller, extra, "template")
        # 3. General
        return (self.seller_discount or 0.0, 0.0, "general")

    def _sync_to_partners(self):
        """Sync condition fields to all partners using this condition.

        ``commercial_condition_id`` is ``company_dependent`` — both the
        ``search()`` for direct users and the ``filtered()`` over group
        members must run with ``with_company(condition.company_id)`` so
        the property values are read in the condition's scope. Without
        it, a sync triggered with ``env.company`` mismatched against
        the condition would either miss partners linked in the
        condition's company or pick up partners linked in a different
        company.
        """
        for condition in self:
            company = condition.company_id or self.env.company
            PartnerCo = self.env["res.partner"].with_company(company)
            # Direct users
            direct = PartnerCo.search([("commercial_condition_id", "=", condition.id)])
            # Group members inheriting: condition belongs to a group head,
            # find members of that group without their own condition
            inherited = self.env["res.partner"]
            group_head = condition.partner_id
            if group_head and group_head.company_group_member_ids:
                members_co = group_head.company_group_member_ids.with_company(company)
                inherited = members_co.filtered(
                    lambda member: not member.commercial_condition_id
                )
            (direct | inherited).with_company(
                company
            )._sync_partner_fields_from_condition()

    @api.constrains("contractual_return")
    def _check_contractual_return(self):
        for condition in self:
            if condition.contractual_return < 0 or condition.contractual_return > 100:
                raise ValidationError(
                    _("Contractual return must be between 0%% and 100%%.")
                )

    @api.constrains("applicable_profile_id", "company_id")
    def _check_applicable_profile_resolved(self):
        """Ensure every condition has a same-company sales profile.

        Two states are blocked:

        - ``applicable_profile_id`` is empty (the resolution chain
          could not find an agent/team/default profile in the
          condition's company) — this surfaces the setup gap to the
          user immediately, instead of letting the order workflow
          break later with a confusing message.
        - ``applicable_profile_id`` belongs to a different company than
          ``company_id``. Defense in depth: the resolver already filters
          by company, but a SQL backdoor or a context-bypassed write
          could still leak a cross-company profile, and the migration
          aggregates these for diagnostics rather than per-record
          aborts.

        Migration runs may set
        ``skip_profile_resolution_check=True`` on the context to
        defer validation to a single aggregated pass after the bulk
        recompute — without this guard the constraint would fire
        per-record during ``flush_recordset`` and only the first error
        would reach the user.
        """
        if self.env.context.get("skip_profile_resolution_check"):
            return
        for condition in self:
            profile = condition.applicable_profile_id
            if not profile:
                raise ValidationError(
                    _(
                        "Could not resolve a sales profile for customer "
                        "'%(partner)s' in company '%(company)s'. This change "
                        "would leave the customer's commercial condition "
                        "without a valid sales profile. Configure another "
                        "agent / team / default profile for this company "
                        "before continuing."
                    )
                    % {
                        "partner": condition.partner_id.display_name or "",
                        "company": condition.company_id.name or "",
                    }
                )
            if profile.company_id != condition.company_id:
                raise ValidationError(
                    _(
                        "Sales profile '%(profile)s' belongs to company "
                        "'%(profile_company)s' but the commercial condition is "
                        "for company '%(condition_company)s'. This is an "
                        "inconsistency — please review the agent/team/default "
                        "profile configuration for this customer and company."
                    )
                    % {
                        "profile": profile.name,
                        "profile_company": profile.company_id.name,
                        "condition_company": condition.company_id.name,
                    }
                )

    @api.model
    def _cron_cleanup_orphan_conditions(self):
        """Delete commercial conditions not referenced by any partner."""
        self.env.cr.execute(
            """
            SELECT cc.id
            FROM partner_commercial_condition cc
            WHERE NOT EXISTS (
                SELECT 1 FROM ir_property ip
                WHERE ip.res_id = CONCAT('res.partner,', cc.partner_id::text)
                  AND ip.name = 'commercial_condition_id'
                  AND ip.value_reference = CONCAT(
                      'partner.commercial.condition,', cc.id::text
                  )
            )
            AND NOT EXISTS (
                SELECT 1 FROM sale_order so
                WHERE so.commercial_condition_id = cc.id
            )
            """
        )
        orphan_ids = [row[0] for row in self.env.cr.fetchall()]
        if orphan_ids:
            orphans = self.browse(orphan_ids)
            _logger.info(
                "Cleaning up %d orphan commercial conditions: %s",
                len(orphans),
                orphans.mapped("partner_id.name"),
            )
            orphans.unlink()


class PartnerCommercialConditionLine(models.Model):
    _name = "partner.commercial.condition.line"
    _description = "Partner Commercial Condition Line"

    condition_id = fields.Many2one(
        comodel_name="partner.commercial.condition",
        required=True,
        ondelete="cascade",
    )
    applied_on = fields.Selection(
        selection=[
            ("product_template", "Product Template"),
            ("product", "Product Variant"),
        ],
        required=True,
        default="product_template",
    )
    product_tmpl_id = fields.Many2one(
        comodel_name="product.template",
        string="Product Template",
        domain="[('sale_ok', '=', True)]",
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="Product Variant",
        domain="[('sale_ok', '=', True)]",
    )
    seller_discount = fields.Float(string="Seller Discount (%)")
    extra_discount = fields.Float(string="Extra Discount (%)")
    band_ids = fields.One2many(
        comodel_name="partner.commercial.condition.line.band",
        inverse_name="line_id",
        string="Quantity Bands",
        help="Additional discount tiers that activate when the order/"
        "invoice line quantity reaches a threshold. The line's direct "
        "Seller/Extra Discount fields are the implicit qty_min=0 band; "
        "bands defined here represent qty thresholds above zero.",
    )
    bands_summary = fields.Text(
        string="Bands",
        compute="_compute_bands_summary",
        help="Human-readable summary of the line's qty bands, shown "
        "in the condition tree so cadastrators can see thresholds at "
        "a glance without opening the line form.",
    )

    @api.depends(
        "band_ids",
        "band_ids.qty_min",
        "band_ids.qty_uom_id",
        "band_ids.seller_discount",
        "band_ids.extra_discount",
    )
    def _compute_bands_summary(self):
        def fmt(val):
            return formatLang(self.env, val, digits=2)

        SELLER_EXTRA_TPL = _("≥%(qty)s %(uom)s → Sell %(sell)s%% / Extra %(ext)s%%")
        SELLER_ONLY_TPL = _("≥%(qty)s %(uom)s → %(sell)s%%")
        for line in self:
            if not line.band_ids:
                line.bands_summary = False
                continue
            parts = []
            for band in line.band_ids.sorted("qty_min"):
                seller = band.seller_discount or 0.0
                extra = band.extra_discount or 0.0
                uom_name = band.qty_uom_id.name or ""
                if extra:
                    parts.append(
                        SELLER_EXTRA_TPL
                        % {
                            "qty": fmt(band.qty_min),
                            "uom": uom_name,
                            "sell": fmt(seller),
                            "ext": fmt(extra),
                        }
                    )
                else:
                    parts.append(
                        SELLER_ONLY_TPL
                        % {
                            "qty": fmt(band.qty_min),
                            "uom": uom_name,
                            "sell": fmt(seller),
                        }
                    )
            line.bands_summary = "\n".join(parts)

    @api.constrains("seller_discount", "extra_discount")
    def _check_seller_discount_markup(self):
        for line in self:
            validate_seller_markup(self.env, line.seller_discount)
            if (line.seller_discount or 0) < 0 and (line.extra_discount or 0) > 0:
                raise ValidationError(
                    _("Extra discount cannot be combined with a seller markup.")
                )

    def _get_band_reference_uom(self):
        """UoM used to normalize bands for comparison.

        Variant line → variant's uom_id. Template line → template's
        uom_id. No product context → False (callers should skip).
        """
        self.ensure_one()
        if self.product_id:
            return self.product_id.uom_id
        if self.product_tmpl_id:
            return self.product_tmpl_id.uom_id
        return False

    def _resolve_discount_for_qty(self, qty, line_uom):
        """Return ``(seller, extra)`` for ``qty`` in ``line_uom``.

        Picks the highest ``qty_min`` ≤ qty across ``band_ids`` after
        UoM normalization to ``line_uom``. Bands whose UoM is in a
        different category than ``line_uom`` (uncomparable) are
        skipped. When no band fits, returns the line's direct
        ``seller_discount``/``extra_discount`` (qty_min=0 implicit
        band).
        """
        self.ensure_one()
        if not self.band_ids:
            return (self.seller_discount or 0.0, self.extra_discount or 0.0)
        candidates = []
        for band in self.band_ids:
            normalized = band._normalize_qty_min(line_uom)
            if normalized is None:
                continue
            if (qty or 0.0) + 1e-9 >= normalized:
                candidates.append((normalized, band))
        if not candidates:
            return (self.seller_discount or 0.0, self.extra_discount or 0.0)
        candidates.sort(key=lambda nb: nb[0])
        winning = candidates[-1][1]
        return (winning.seller_discount or 0.0, winning.extra_discount or 0.0)

    @api.onchange("applied_on")
    def _onchange_applied_on(self):
        if self.applied_on == "product_template":
            self.product_id = False
        if self.applied_on == "product":
            self.product_tmpl_id = False

    def action_open_band_form(self):
        """Open the line's form view so the user can manage band_ids.

        The tree is editable inline for the simple seller/extra fields;
        bands live on a nested tree inside the line form, so this
        button is the entry point. Forces the dedicated form view so
        the dialog doesn't fall back to Odoo's auto-generated form
        (which exposes ``condition_id`` and other context fields).
        """
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "name": _("Manage Bands"),
            "views": [
                (
                    self.env.ref(
                        "tr_commercial_policy." "partner_commercial_condition_line_form"
                    ).id,
                    "form",
                )
            ],
        }

    def _get_applicable_profile(self, condition=None):
        """Delegate to parent condition to resolve partner's profile.

        ``condition`` overrides self.condition_id (needed during create).
        Falls back to env.company when no condition is available (e.g.
        during create before condition_id is set).
        """
        condition = condition or (self.condition_id if self else False)
        if condition:
            return condition._get_applicable_profile()
        # No condition context — use env.company as last resort
        return self.env.company.default_sales_profile_id or self.env["tr.sales.profile"]

    def _validate_line_discount_limits(self, vals, condition=None):
        """Validate line-level discounts against user profile.

        Directors bypass all validations.
        """
        if self.env.user.has_group("tr_commercial_policy.group_sales_director"):
            return

        discount_fields = {"seller_discount", "extra_discount"}
        if not discount_fields.intersection(vals):
            return

        profile = self._get_applicable_profile(condition=condition)
        if not profile:
            raise AccessError(
                _("You need a sales profile to edit commercial conditions.")
            )

        # Extra discount requires manager/director
        extra_discount = vals.get("extra_discount", 0.0)
        if extra_discount and extra_discount > 0:
            if not self.env.user.has_group("tr_commercial_policy.group_sales_manager"):
                raise AccessError(
                    _(
                        "Extra discount on commercial conditions requires "
                        "manager or director approval."
                    )
                )

        # Seller discount against profile rule
        if "seller_discount" in vals:
            seller_disc = vals["seller_discount"]
            # Resolve the best matching rule
            product_id = vals.get("product_id") or (
                self.product_id.id if self else False
            )
            product_tmpl_id = vals.get("product_tmpl_id") or (
                self.product_tmpl_id.id if self else False
            )
            rule = self._resolve_rule_for_product(profile, product_id, product_tmpl_id)
            if rule and seller_disc > rule.seller_discount_max:
                raise ValidationError(  # noqa: UP031
                    _(
                        "Seller discount (%(disc).2f%%) exceeds"
                        " the maximum allowed (%(max).2f%%) by"
                        " your sales profile '%(profile)s'."
                    )
                    % {
                        "disc": seller_disc,
                        "max": rule.seller_discount_max,
                        "profile": profile.name,
                    }
                )

    def _resolve_rule_for_product(self, profile, product_id, product_tmpl_id):
        """Find the best matching profile rule for a product.

        Used to validate ``seller_discount`` on commercial condition lines —
        a static configuration context with no line quantity available.
        Volume rules (``qty_min > 0``) are intentionally filtered out: they
        only apply at order/invoice time, when the line quantity is known.
        The cadastrador of a condition line should respect the base limit
        (rule with ``qty_min == 0``); volume bands extend that limit later.
        """
        rules = profile.rule_ids.filtered(lambda r: r.qty_min == 0)
        if product_id:
            product = self.env["product.product"].browse(product_id)
            variant_rule = rules.filtered(
                lambda rule: rule.applied_on == "product"
                and rule.product_id.id == product_id
            )
            if variant_rule:
                return variant_rule[0]
            tmpl_id = product.product_tmpl_id.id
            tmpl_rule = rules.filtered(
                lambda rule: rule.applied_on == "product_template"
                and rule.product_tmpl_id.id == tmpl_id
            )
            if tmpl_rule:
                return tmpl_rule[0]
            categ = product.categ_id
            while categ:
                categ_rule = rules.filtered(
                    lambda rule, cat=categ: rule.applied_on == "category"
                    and rule.categ_id == cat
                )
                if categ_rule:
                    return categ_rule[0]
                categ = categ.parent_id
        elif product_tmpl_id:
            tmpl_rule = rules.filtered(
                lambda rule: rule.applied_on == "product_template"
                and rule.product_tmpl_id.id == product_tmpl_id
            )
            if tmpl_rule:
                return tmpl_rule[0]
        # General
        general_rule = rules.filtered(lambda rule: rule.applied_on == "general")
        return general_rule[0] if general_rule else False

    def _line_chatter_label(self):
        self.ensure_one()
        if self.applied_on == "product" and self.product_id:
            return self.product_id.display_name
        if self.product_tmpl_id:
            return self.product_tmpl_id.display_name
        return _("(no product)")

    def _post_chatter(self, header, changes=None, values=None):
        self.ensure_one()
        if _chatter_suppressed(self.env):
            return
        body = "<p>%s</p>" % html_escape(header)
        if changes:
            body += _render_changes_html(self, changes)
        elif values:
            body += _render_values_html(self, values)
        self.condition_id._message_log(body=body)

    @api.model_create_multi
    def create(self, vals_list):
        Condition = self.env["partner.commercial.condition"]
        for vals in vals_list:
            condition = Condition.browse(vals.get("condition_id")).exists()
            self._validate_line_discount_limits(vals, condition=condition)
        records = super().create(vals_list)
        for line in records:
            values = [
                (_field_label(line, f), _format_tracked_value(line, f, line[f]))
                for f in _LINE_TRACKED_FIELDS
                if line[f]
            ]
            line._post_chatter(
                _("Product line added: %s") % line._line_chatter_label(),
                values=values,
            )
        return records

    def write(self, vals):
        tracked = [f for f in _LINE_TRACKED_FIELDS if f in vals]
        old_data = {}
        if tracked:
            for line in self:
                old_data[line.id] = {f: line[f] for f in tracked}
        self._validate_line_discount_limits(vals)
        res = super().write(vals)
        if tracked:
            for line in self:
                changes = []
                for f in tracked:
                    old = old_data[line.id][f]
                    new = line[f]
                    if line._fields[f].type == "many2one":
                        if old.id == new.id:
                            continue
                    elif old == new:
                        continue
                    changes.append(
                        (
                            _field_label(line, f),
                            _format_tracked_value(line, f, old),
                            _format_tracked_value(line, f, new),
                        )
                    )
                if changes:
                    line._post_chatter(
                        _("Product line updated: %s") % line._line_chatter_label(),
                        changes,
                    )
        return res

    def unlink(self):
        suppressed = _chatter_suppressed(self.env)
        snapshots = (
            []
            if suppressed
            else [(line.condition_id, line._line_chatter_label()) for line in self]
        )
        res = super().unlink()
        for condition, label in snapshots:
            if condition.exists():
                condition._message_log(
                    body="<p>%s</p>"
                    % html_escape(_("Product line removed: %s") % label)
                )
        return res

    @api.constrains("applied_on", "product_id", "product_tmpl_id")
    def _check_applied_on_product(self):
        for line in self:
            if line.applied_on == "product_template" and line.product_id:
                raise ValidationError(
                    _(
                        "Line applied on 'Product Template' must not"
                        " have a product variant set."
                    )
                )
            if line.applied_on == "product" and line.product_tmpl_id:
                raise ValidationError(
                    _(
                        "Line applied on 'Product Variant' must not"
                        " have a product template set."
                    )
                )

    @api.constrains("product_id", "product_tmpl_id", "condition_id")
    def _check_unique_product(self):
        for line in self:
            domain = [
                ("condition_id", "=", line.condition_id.id),
                ("id", "!=", line.id),
            ]
            if line.product_id:
                domain.append(("product_id", "=", line.product_id.id))
                if self.search_count(domain):
                    raise ValidationError(  # noqa: UP031
                        _(
                            "A line for product variant"
                            " '%(product)s' already exists"
                            " in this condition."
                        )
                        % {"product": line.product_id.display_name}
                    )
            elif line.product_tmpl_id:
                domain.append(("product_tmpl_id", "=", line.product_tmpl_id.id))
                domain.append(("product_id", "=", False))
                if self.search_count(domain):
                    raise ValidationError(  # noqa: UP031
                        _(
                            "A line for product template"
                            " '%(product)s' already exists"
                            " in this condition."
                        )
                        % {"product": line.product_tmpl_id.display_name}
                    )


class PartnerCommercialConditionLineBand(models.Model):
    """Quantity tier on a commercial condition line.

    Represents "if the line quantity reaches X, apply discount Y%".
    Bands extend the line's direct ``seller_discount``/
    ``extra_discount`` (which act as the implicit qty_min=0 band).
    Resolution at order/invoice time picks the highest ``qty_min``
    that the line quantity reaches, after UoM normalization.
    """

    _name = "partner.commercial.condition.line.band"
    _description = "Partner Commercial Condition Line Band"
    _order = "line_id, qty_min"

    line_id = fields.Many2one(
        comodel_name="partner.commercial.condition.line",
        required=True,
        ondelete="cascade",
        index=True,
    )
    qty_min = fields.Float(
        string="Min Qty",
        required=True,
        help="Minimum line quantity (in ``Qty UoM``) to activate this "
        "band. Must be greater than zero — qty_min=0 is the line's "
        "direct discount fields.",
    )
    qty_uom_id = fields.Many2one(
        comodel_name="uom.uom",
        string="Qty UoM",
        required=True,
        default=lambda self: self._default_qty_uom_id(),
        help="Unit of measure of ``Min Qty``. The order/invoice line "
        "quantity is converted into this UoM before comparison. Must "
        "be in the same UoM category as the product's UoM.",
    )
    seller_discount = fields.Float(string="Seller Discount (%)")
    extra_discount = fields.Float(string="Extra Discount (%)")

    @api.model
    def _default_qty_uom_id(self):
        line = self.env["partner.commercial.condition.line"].browse(
            self.env.context.get("default_line_id")
        )
        ref = line._get_band_reference_uom() if line else False
        return ref.id if ref else False

    @api.onchange("qty_min", "line_id")
    def _onchange_qty_min_default_uom(self):
        """Fill ``qty_uom_id`` from the line's product when blank.

        The model-level default works only when the band is created
        from a saved line; in the inline form (band added before
        the line is saved), the default callback can't read
        ``line_id``. This onchange fills it once the user types a
        ``qty_min`` value, mirroring the equivalent helper on
        ``tr.sales.profile.rule``.
        """
        # ref always resolves once line has product/template; assigning
        # an empty recordset is a harmless no-op when not, so no extra
        # guard is needed.
        for band in self:
            if band.qty_uom_id:
                continue
            band.qty_uom_id = band.line_id._get_band_reference_uom()

    @api.constrains("qty_min")
    def _check_qty_min_positive(self):
        for band in self:
            if (band.qty_min or 0.0) <= 0:
                raise ValidationError(
                    _(
                        "Band quantity threshold must be greater than zero. Use the line's Seller/Extra Discount fields for the qty_min=0 base discount."
                    )
                )

    @api.constrains("qty_uom_id", "line_id")
    def _check_qty_uom_category(self):
        # qty_uom_id is required at the field level and the line's
        # _check_applied_on_product constraint enforces exactly one of
        # product_id/product_tmpl_id is set, so ref_uom always
        # resolves to a real UoM.
        for band in self:
            ref_uom = band.line_id._get_band_reference_uom()
            if ref_uom.category_id != band.qty_uom_id.category_id:
                raise ValidationError(
                    _(
                        "Band UoM (%(band_uom)s) must be in the same category as the product's UoM (%(prod_uom)s)."
                    )
                    % {"band_uom": band.qty_uom_id.name, "prod_uom": ref_uom.name}
                )

    @api.constrains("seller_discount", "extra_discount")
    def _check_seller_discount_markup(self):
        for band in self:
            validate_seller_markup(self.env, band.seller_discount)
            if (band.seller_discount or 0) < 0 and (band.extra_discount or 0) > 0:
                raise ValidationError(
                    _("Extra discount cannot be combined with a seller markup.")
                )

    @api.constrains("qty_min", "qty_uom_id", "line_id")
    def _check_qty_min_unique_on_line(self):
        """Two bands resolving to the same threshold (after UoM normalization) is configuration-ambiguous — reject it."""
        DUP_MSG = _(
            "Two bands resolve to the same threshold (%(qty).2f %(uom)s). Bands must define distinct quantity thresholds."
        )
        # ref_uom always resolves (line constrains exactly one of
        # product/template). _normalize_qty_min only returns None if
        # the band's qty_uom_id is in a different category — also
        # blocked upstream by _check_qty_uom_category.
        for band in self:
            ref_uom = band.line_id._get_band_reference_uom()
            this_normalized = band._normalize_qty_min(ref_uom)
            for other in band.line_id.band_ids - band:
                other_normalized = other._normalize_qty_min(ref_uom)
                if abs(other_normalized - this_normalized) < 1e-6:
                    raise ValidationError(
                        DUP_MSG % {"qty": this_normalized, "uom": ref_uom.name}
                    )

    def _normalize_qty_min(self, target_uom):
        """Return ``qty_min`` converted to ``target_uom``, or None when
        conversion is impossible (different UoM category)."""
        self.ensure_one()
        if not self.qty_uom_id or not target_uom:
            return None
        if self.qty_uom_id == target_uom:
            return self.qty_min or 0.0
        if self.qty_uom_id.category_id != target_uom.category_id:
            return None
        return self.qty_uom_id._compute_quantity(
            self.qty_min or 0.0, target_uom, round=False
        )

    def _band_chatter_label(self):
        self.ensure_one()
        return _("≥%(qty)s %(uom)s on %(line)s") % {
            "qty": formatLang(self.env, self.qty_min or 0.0, digits=2),
            "uom": self.qty_uom_id.name or "",
            "line": self.line_id._line_chatter_label() if self.line_id else "",
        }

    def _post_chatter(self, header, changes=None, values=None):
        self.ensure_one()
        if _chatter_suppressed(self.env):
            return
        body = "<p>%s</p>" % html_escape(header)
        if changes:
            body += _render_changes_html(self, changes)
        elif values:
            body += _render_values_html(self, values)
        self.line_id.condition_id._message_log(body=body)

    @api.model_create_multi
    def create(self, vals_list):
        bands = super().create(vals_list)
        bands._validate_against_profile()
        for band in bands:
            values = [
                (_field_label(band, f), _format_tracked_value(band, f, band[f]))
                for f in _BAND_TRACKED_FIELDS
                if band[f]
            ]
            band._post_chatter(
                _("Quantity band added: %s") % band._band_chatter_label(),
                values=values,
            )
        return bands

    def write(self, vals):
        tracked = [f for f in _BAND_TRACKED_FIELDS if f in vals]
        old_data = {}
        if tracked:
            for band in self:
                old_data[band.id] = {f: band[f] for f in tracked}
        res = super().write(vals)
        # ``line_id`` change moves the band to a different product /
        # template, which switches the applicable profile and the
        # extra_discount gate context — must re-validate.
        if {
            "qty_min",
            "qty_uom_id",
            "seller_discount",
            "extra_discount",
            "line_id",
        } & set(vals):
            self._validate_against_profile()
        if tracked:
            for band in self:
                changes = []
                for f in tracked:
                    old = old_data[band.id][f]
                    new = band[f]
                    if band._fields[f].type == "many2one":
                        if old.id == new.id:
                            continue
                    elif old == new:
                        continue
                    changes.append(
                        (
                            _field_label(band, f),
                            _format_tracked_value(band, f, old),
                            _format_tracked_value(band, f, new),
                        )
                    )
                if changes:
                    band._post_chatter(
                        _("Quantity band updated: %s") % band._band_chatter_label(),
                        changes,
                    )
        return res

    def unlink(self):
        suppressed = _chatter_suppressed(self.env)
        snapshots = (
            []
            if suppressed
            else [
                (band.line_id.condition_id, band._band_chatter_label()) for band in self
            ]
        )
        res = super().unlink()
        for condition, label in snapshots:
            if condition.exists():
                condition._message_log(
                    body="<p>%s</p>"
                    % html_escape(_("Quantity band removed: %s") % label)
                )
        return res

    def _validate_against_profile(self):
        """Validate each band's ``seller_discount`` against the user's profile, qty-aware. Director bypasses; template-line bands use first active variant as proxy. Mirrors the line-level extra_discount gate: extra_discount > 0 requires manager or director."""
        if self.env.user.has_group("tr_commercial_policy.group_sales_director"):
            return
        # Mirror line-level gate: extra discount on a band is a
        # negotiated concession; require manager/director just like
        # the line itself does in _validate_line_discount_limits.
        is_manager = self.env.user.has_group("tr_commercial_policy.group_sales_manager")
        if not is_manager:
            for band in self:
                if (band.extra_discount or 0.0) > 0:
                    raise AccessError(
                        _(
                            "Extra discount on a band requires manager or "
                            "director approval."
                        )
                    )
        OVER_MSG = _(
            "Band seller discount (%(disc).2f%%) exceeds the maximum allowed (%(max).2f%%) by your sales profile '%(profile)s' for qty ≥ %(qty).2f %(uom)s."
        )
        for band in self:
            line = band.line_id
            condition = line.condition_id
            profile = line._get_applicable_profile(condition=condition)
            if not profile:
                continue
            product_arg = False
            if line.applied_on == "product" and line.product_id:
                product_arg = line.product_id
            elif line.applied_on == "product_template" and line.product_tmpl_id:
                product_arg = line.product_tmpl_id.product_variant_ids.filtered(
                    "active"
                )[:1]
            if not product_arg:
                continue
            rule = self._resolve_profile_rule_qty_aware(
                profile, product_arg, band.qty_min, band.qty_uom_id
            )
            if not rule:
                continue
            seller = band.seller_discount or 0.0
            if seller > rule.seller_discount_max:
                raise ValidationError(
                    OVER_MSG
                    % {
                        "disc": seller,
                        "max": rule.seller_discount_max,
                        "profile": profile.name,
                        "qty": band.qty_min,
                        "uom": band.qty_uom_id.name,
                    }
                )

    def _resolve_profile_rule_qty_aware(self, profile, product, qty, uom):
        """Find the profile rule that applies to ``product`` at ``qty``: variant → template → category → general."""

        def _qty_min_normalized(rule):
            if not rule.qty_uom_id or not uom:
                return rule.qty_min or 0.0
            if rule.qty_uom_id == uom:
                return rule.qty_min or 0.0
            if rule.qty_uom_id.category_id != uom.category_id:
                return None
            return rule.qty_uom_id._compute_quantity(
                rule.qty_min or 0.0, uom, round=False
            )

        def _pick_best(rules):
            candidates = []
            for r in rules:
                normalized = _qty_min_normalized(r)
                if normalized is None:
                    continue
                if (qty or 0.0) + 1e-9 >= normalized:
                    candidates.append((normalized, r))
            if not candidates:
                return False
            candidates.sort(key=lambda nr: nr[0])
            return candidates[-1][1]

        rules = profile.rule_ids
        variant_rules = rules.filtered(
            lambda r: r.applied_on == "product" and r.product_id == product
        )
        winner = _pick_best(variant_rules)
        if winner:
            return winner
        tmpl_rules = rules.filtered(
            lambda r: r.applied_on == "product_template"
            and r.product_tmpl_id == product.product_tmpl_id
        )
        winner = _pick_best(tmpl_rules)
        if winner:
            return winner
        categ = product.categ_id
        while categ:
            categ_rules = rules.filtered(
                lambda r, c=categ: r.applied_on == "category" and r.categ_id == c
            )
            winner = _pick_best(categ_rules)
            if winner:
                return winner
            categ = categ.parent_id
        general_rules = rules.filtered(lambda r: r.applied_on == "general")
        return _pick_best(general_rules)

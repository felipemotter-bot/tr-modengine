# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import threading

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

MANAGER_GROUP_XMLID = "tr_commercial_policy.group_sales_manager"
REP_GROUP_XMLID = "tr_sales_rep_access.group_sales_rep_external"
_GROUPS_NO_REP = "!tr_sales_rep_access.group_sales_rep_external"
DEFAULT_CATALOG_PARAM = "tr_sales_rep_access.tr_sales_rep_default_category_ids"
DRAFT_STAGE_XMLID = "partner_stage.partner_stage_draft"

# Thread-local flag set by the module's own ``create`` override so
# that side-effect writes fired by inverses/computes during the
# create (e.g. ``l10n_br_fiscal._inverse_fiscal_profile`` touching
# ``tax_framework`` on a parent Active partner) are not blocked by
# the PR 4b write guard. The guard is about external writes from
# the rep (UI / RPC), not about framework-triggered propagation
# inside a single ``create`` call. Using a thread-local here
# instead of ``self.env.context`` closes a bypass that a context
# key would open: an attacker could otherwise call
# ``.with_context(FLAG=True).write(...)`` via RPC and bypass the
# guard. Thread-locals are not reachable from outside the Python
# process.
_tls = threading.local()


def _in_create_scope():
    return getattr(_tls, "tr_sales_rep_access_in_create", False)


# Fields that a rep is still allowed to write on an Active partner,
# because they are not business data but chatter / scheduled
# activities. Every other field must go through a
# ``tr.partner.change.request`` (cadastral fields) or through the
# ``tr_commercial_policy`` action buttons (commercial condition).
# Internal writes by this module use ``.sudo()`` so the guard is a
# no-op for them (``env.user`` becomes SUPERUSER).
REP_ACTIVE_WRITE_ALLOWLIST = frozenset(
    {
        # mail.thread
        "message_ids",
        "message_follower_ids",
        "message_partner_ids",
        "message_main_attachment_id",
        "website_message_ids",
        "message_unread",
        "message_unread_counter",
        "message_needaction",
        "message_needaction_counter",
        "message_has_error",
        "message_has_error_counter",
        "message_attachment_count",
        # mail.activity.mixin
        "activity_ids",
        "activity_state",
        "activity_user_id",
        "activity_type_id",
        "activity_type_icon",
        "activity_date_deadline",
        "activity_summary",
        "activity_exception_decoration",
        "activity_exception_icon",
        "activity_calendar_event_id",
    }
)


def _allowed_rep_agent_commands(rep_partner_id):
    """Return the canonical x2many command lists that, applied to an
    empty relation, assign the acting rep as the single agent.

    Three forms legitimately produced by Odoo clients and imports:
    ``Command.set([rep])`` → ``(6, 0, [rep])``,
    ``Command.link(rep)`` → ``(4, rep, 0)`` and the legacy short
    ``(4, rep)``. Anything else (clear, inline create, update,
    unlink, multiple commands, foreign id) fails the equality check
    and must raise.
    """
    return (
        [(6, 0, [rep_partner_id])],
        [(4, rep_partner_id, 0)],
        [(4, rep_partner_id)],
    )


class ResPartner(models.Model):
    _inherit = "res.partner"

    allowed_category_ids = fields.Many2many(
        comodel_name="product.category",
        relation="tr_sales_rep_access_partner_allowed_category_rel",
        column1="partner_id",
        column2="category_id",
        string="Allowed Product Categories",
        groups=MANAGER_GROUP_XMLID,
        help=(
            "Product categories the rep is allowed to sell from. "
            "Descendants are implicitly included; combined with "
            "excluded_category_ids to compute the effective scope."
        ),
    )
    excluded_category_ids = fields.Many2many(
        comodel_name="product.category",
        relation="tr_sales_rep_access_partner_excluded_category_rel",
        column1="partner_id",
        column2="category_id",
        string="Excluded Product Categories",
        groups=MANAGER_GROUP_XMLID,
        help=(
            "Categories explicitly excluded from the allowed scope, "
            "including their descendants. Use to carve out a subtree "
            "while keeping its parent in allowed."
        ),
    )

    # PR 8 — chatter hidden for reps (same pattern as sale.order).
    # PR 8 / PR 12 — chatter hidden for reps. PR 12 extends to auxiliary
    # mail.thread fields so that metadata is also blocked via RPC.
    message_ids = fields.One2many(groups=_GROUPS_NO_REP)
    message_follower_ids = fields.One2many(groups=_GROUPS_NO_REP)
    message_is_follower = fields.Boolean(groups=_GROUPS_NO_REP)
    message_partner_ids = fields.Many2many(groups=_GROUPS_NO_REP)
    has_message = fields.Boolean(groups=_GROUPS_NO_REP)
    message_needaction = fields.Boolean(groups=_GROUPS_NO_REP)
    message_needaction_counter = fields.Integer(groups=_GROUPS_NO_REP)
    message_has_error = fields.Boolean(groups=_GROUPS_NO_REP)
    message_has_error_counter = fields.Integer(groups=_GROUPS_NO_REP)
    message_attachment_count = fields.Integer(groups=_GROUPS_NO_REP)
    message_main_attachment_id = fields.Many2one(groups=_GROUPS_NO_REP)

    # -----------------------------------------------------------------
    # PR 7 commit 1 — ``eng_partner_sales_info`` server-side hide.
    #
    # The companion module exposes 21 aggregated sales-statistics
    # fields on ``res.partner`` (last_order_id, order_count,
    # total_ordered, average_ordered, days_since_last_order, etc.).
    # Upstream already hides the analysis tab via
    # ``eng_partner_sales_info.group_partner_sales_analysis``, but
    # that is view-only — the fields stayed open to RPC read /
    # fields_get / search. Adding field-level ``groups=`` that
    # excludes the rep closes the RPC path. DA-6: never trust
    # view-level groups as security.
    # -----------------------------------------------------------------

    last_order_id = fields.Many2one(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    last_order_date = fields.Date(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    last_order_status = fields.Char(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    order_count = fields.Integer(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    total_ordered = fields.Monetary(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    average_ordered = fields.Monetary(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    average_ordered_no_discrepancies = fields.Monetary(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    average_time_between_orders = fields.Float(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    days_since_last_order = fields.Integer(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    last_invoice_date = fields.Date(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    invoice_count = fields.Integer(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    total_invoiced = fields.Monetary(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    average_invoiced = fields.Monetary(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    average_invoiced_no_discrepancies = fields.Monetary(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    average_time_between_invoices = fields.Float(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    last_invoice_id = fields.Many2one(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    days_since_last_invoice = fields.Integer(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    analysis_message = fields.Text(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    has_open_quotation = fields.Boolean(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    last_open_quotation_id = fields.Many2one(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    last_open_quotation_date = fields.Date(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )

    # PR 5 — server-side hide: non-operational sensitive fields that
    # rep should not read nor write via RPC. Fields are redeclared
    # here only to add ``groups=`` restriction; the original
    # declaration in each upstream module stays untouched (Odoo 16
    # merges the attributes). Fiscal operational fields
    # (``tax_framework``, ``fiscal_profile_id``, ``ind_ie_dest``,
    # ``ind_final``, ``vat``, etc.) are NOT touched by this PR —
    # they are read inside ``l10n_br_fiscal`` onchanges during order
    # creation, so server-side hide would break the rep's sale flow.
    # Hiding them is tracked for PR 5b (dedicated approach with
    # targeted XPaths per subview or sudo-wrapped onchange
    # overrides); see AGENTS.md "Server-side hide of non-operational
    # sensitive fields (PR 5)" for the full deferral context.

    # ---- partner_capital ----
    capital_amount = fields.Monetary(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    capital_currency_id = fields.Many2one(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    turnover_range_id = fields.Many2one(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    turnover_amount = fields.Float(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    company_size = fields.Selection(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )

    # ---- l10n_br_sped_base ----
    is_accountant = fields.Boolean(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    crc_code = fields.Char(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )
    crc_state_id = fields.Many2one(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )

    # ---- l10n_br_account_withholding ----
    wh_cityhall = fields.Boolean(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )

    # ---- l10n_br_hr ----
    union_entity_code = fields.Char(
        groups="!tr_sales_rep_access.group_sales_rep_external",
    )

    @api.model_create_multi
    def create(self, vals_list):
        default_ids = self._sales_rep_default_catalog_ids()
        is_rep = self.env.user.has_group(REP_GROUP_XMLID)
        draft_stage = (
            self.env.ref(DRAFT_STAGE_XMLID, raise_if_not_found=False)
            if is_rep
            else None
        )
        rep_partner_id = self.env.user.partner_id.id if is_rep else None
        for vals in vals_list:
            # PR 2 — default catalog
            if default_ids and vals.get("agent") and "allowed_category_ids" not in vals:
                vals["allowed_category_ids"] = [(6, 0, default_ids)]
            if is_rep and rep_partner_id and not vals.get("parent_id"):
                # PR 3 — rep-created commercials must end up with
                # the acting rep as the single agent. If the caller
                # did not pass ``agent_ids``, auto-populate; if they
                # did, validate that the commands resolve to exactly
                # ``{rep_partner_id}`` — otherwise raise. We validate
                # here (before ``super``) instead of after because
                # ``mail.thread.create`` reads the record in the
                # rep's env and the ``res.partner`` rule would hide
                # a customer assigned to someone else, turning our
                # ValidationError into an AccessError from core.
                if "agent_ids" in vals:
                    # JSON-RPC clients may send the commands as lists
                    # of lists rather than lists of tuples; normalize
                    # each command to a tuple so the whitelist
                    # comparison stays literal but tolerant of the
                    # wire format.
                    normalized = [tuple(command) for command in vals["agent_ids"]]
                    if normalized not in _allowed_rep_agent_commands(rep_partner_id):
                        raise ValidationError(
                            _(
                                "A customer created by a sales "
                                "representative must be assigned to "
                                "the acting representative. You "
                                "cannot set a different agent on a "
                                "new customer."
                            )
                        )
                else:
                    vals["agent_ids"] = [(4, rep_partner_id)]
                # PR 3 — force Draft stage for new commercial
                # partners created by a rep, overriding any value
                # explicitly passed. RPC/import could otherwise
                # send ``stage_id=active`` and bypass the tier
                # workflow. Child contacts (parent_id set) are not
                # forced — protection against selling to an
                # unapproved customer lives in sale.order's guard
                # against ``commercial_partner_id.state``.
                if draft_stage:
                    vals["stage_id"] = draft_stage.id
        # Thread-local flag telling the PR 4b write guard to stand
        # down on side-effect writes fired inside this create
        # (e.g. ``l10n_br_fiscal`` inverses that touch fiscal fields
        # on the parent Active partner via _onchange hooks). A
        # context key would be RPC-controllable and let a rep
        # bypass the guard; thread-local is only reachable from
        # this Python process.
        previous = getattr(_tls, "tr_sales_rep_access_in_create", False)
        _tls.tr_sales_rep_access_in_create = True
        try:
            records = super().create(vals_list)
        finally:
            _tls.tr_sales_rep_access_in_create = previous
        # Fire tier reviews immediately for new commercial partners
        # created by a rep that match the tier_definition domain,
        # so the reviewer list shows up right after creation
        # without requiring a manual "Request validation" click.
        # Child contacts are excluded — the workflow applies only
        # to new commercial partners.
        if is_rep:
            records.filtered(
                lambda p: p.state == "draft" and p.agent_ids and not p.parent_id
            ).request_validation()
        return records

    def write(self, vals):
        # PR 4b — rep cannot write directly on Active partners. The
        # canonical path is a ``tr.partner.change.request`` for
        # cadastral fields and the action buttons on
        # ``tr_commercial_policy`` for the commercial condition (those
        # actions use ``.sudo()`` to write ``commercial_condition_id``
        # and therefore bypass this guard naturally). Partners still
        # in Draft (pre-approval, PR 3 flow) remain freely editable.
        if self._sales_rep_should_block_active_write(vals):
            raise AccessError(
                _(
                    "As a sales representative, you cannot modify an "
                    "Active customer directly. Use the 'Request field "
                    "update' or 'Request new contact' buttons to open "
                    "a change request for the Sales Manager to approve."
                )
            )
        if self.env.user.has_group(REP_GROUP_XMLID) and not self.env.su:
            # PR 8 — suppress tracking for rep writes on Draft partners
            # to avoid chatter pollution (same trade-off as sale.order).
            return super(ResPartner, self.with_context(mail_notrack=True)).write(vals)
        return super().write(vals)

    def _sales_rep_should_block_active_write(self, vals):
        """Return True when this write must be rejected for a rep.

        - Non-rep users are unaffected.
        - Internal writes by this module use ``.sudo()`` already, so
          ``env.user`` is SUPERUSER and ``has_group`` returns False.
        - Writes limited to ``mail.thread`` / ``mail.activity.mixin``
          fields are allowed (chatter and activities are not business
          data — rep continues to post comments and schedule
          activities on any visible partner).
        - Partners still in Draft (pre-approval) stay fully editable.
        - Active (``state == 'confirmed'``) partners reject any
          business-field write. Child partners follow the same rule
          because they inherit the Active stage via ``partner_stage``
          defaults; edition of existing child contacts is an
          intentional limitation tracked for a future PR (``change_-
          request`` would need a ``field_update_child`` variant).
        """
        if self.env.su:
            # SUPERUSER / ``sudo()`` bypasses the guard: any caller
            # that uses ``.sudo()`` is explicitly a system-controlled
            # path (e.g. ``tr_commercial_policy`` action buttons,
            # ``_apply_field_update`` / ``_create_child`` on an
            # approved change request).
            return False
        if _in_create_scope():
            return False
        if not self.env.user.has_group(REP_GROUP_XMLID):
            return False
        sensitive = set(vals) - REP_ACTIVE_WRITE_ALLOWLIST
        if not sensitive:
            return False
        for partner in self:
            if partner.state == "confirmed":
                return True
        return False

    @api.model
    def _sales_rep_default_catalog_ids(self):
        """Return the configured default catalog as a list of IDs.

        Reads from ``ir.config_parameter`` because Many2many cannot use
        the ``config_parameter`` template in ``res.config.settings``.
        Stored as a CSV of integer ids. Ignores IDs whose category no
        longer exists.
        """
        raw = (
            self.env["ir.config_parameter"].sudo().get_param(DEFAULT_CATALOG_PARAM, "")
        )
        if not raw:
            return []
        try:
            ids = [int(x) for x in raw.split(",") if x.strip()]
        except ValueError:
            return []
        Category = self.env["product.category"].sudo()
        return Category.browse(ids).exists().ids

    def _get_visible_category_ids(self):
        """Return the recordset of categories visible to this agent.

        Computed on-the-fly as
        ``descendants(allowed_category_ids) − descendants(
        excluded_category_ids)`` using the native ``child_of``
        operator (indexed via ``parent_path``). Callers use ``.ids``
        on the result to build domains.

        If ``allowed_category_ids`` is empty, returns an empty
        recordset — the fail-safe meaning "sees nothing".
        """
        self.ensure_one()
        Category = self.env["product.category"]
        if not self.allowed_category_ids:
            return Category
        allowed = Category.search([("id", "child_of", self.allowed_category_ids.ids)])
        if not self.excluded_category_ids:
            return allowed
        excluded = Category.search([("id", "child_of", self.excluded_category_ids.ids)])
        return allowed - excluded

    # -----------------------------------------------------------------
    # PR 7 commit 3 — ``tr_pricelist_report`` server-side guard.
    #
    # Single chokepoint: ``action_print_pricelist_from_menu`` in
    # upstream already delegates to this method, so guarding here
    # covers both the header button on the partner form and the
    # action-menu server binding (upstream exposes this method with
    # ``binding_model_id = res.partner`` on list/form view types).
    # The view hide of the button is defense in depth only; this
    # server-side guard is what actually blocks the rep.
    # -----------------------------------------------------------------

    def action_print_pricelist(self):
        if self.env.user.has_group(REP_GROUP_XMLID):
            raise AccessError(
                _(
                    "Sales reps are not allowed to generate the partner "
                    "price list report — the wizard would expose products "
                    "outside the rep's allowed catalog."
                )
            )
        return super().action_print_pricelist()

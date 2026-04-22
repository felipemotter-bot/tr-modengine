# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

REP_GROUP_XMLID = "tr_sales_rep_access.group_sales_rep_external"
MANAGER_GROUP_XMLID = "tr_commercial_policy.group_sales_manager"

# Private context flag set by action_approve / action_reject /
# action_cancel before writing to ``state``. A plain RPC write
# without this flag is rejected, to keep the transitions flowing
# exclusively through the button handlers (which enforce manager
# group, tier validation, review cleanup and partner propagation).
INTERNAL_CTX_KEY = "_tr_change_request_internal_transition"

# Mapping: form field on the change request ↔ field on res.partner.
# Drives the prefill onchange and the apply helper. Adding a new
# editable field means declaring it here and on the form — nothing
# else.
_FIELD_UPDATE_MAP = {
    "new_name": "name",
    "new_phone": "phone",
    "new_mobile": "mobile",
    "new_email": "email",
    "new_street": "street",
    "new_street2": "street2",
    "new_city": "city",
    "new_zip": "zip",
    "new_state_id": "state_id",
    "new_country_id": "country_id",
}

_FIELD_UPDATE_FORM_FIELDS = frozenset(_FIELD_UPDATE_MAP)
_NEW_CHILD_FIELDS = frozenset(
    {
        "new_child_name",
        "new_child_email",
        "new_child_phone",
        "new_child_mobile",
        "new_child_function",
        "new_child_type",
    }
)

IMMUTABLE_FIELDS_AFTER_PENDING = (
    frozenset({"partner_id", "request_type", "reason"})
    | _FIELD_UPDATE_FORM_FIELDS
    | _NEW_CHILD_FIELDS
)

# Audit fields set at create; the per-group record rule relies on
# ``sales_rep_partner_id`` and the audit trail relies on
# ``requested_by``. Rewriting either via RPC would let a rep move a
# request to another rep's scope or forge authorship, so both are
# frozen after create — not even the module itself writes them again.
IMMUTABLE_AUDIT_FIELDS = frozenset({"requested_by", "sales_rep_partner_id"})


class TrPartnerChangeRequest(models.Model):
    _name = "tr.partner.change.request"
    _description = "Partner Change Request"
    _inherit = ["mail.thread", "tier.validation"]
    _order = "create_date desc, id desc"

    _state_from = ["pending"]
    _state_to = ["approved"]
    _cancel_state = "cancelled"

    name = fields.Char(compute="_compute_name", store=True)
    partner_id = fields.Many2one(
        "res.partner",
        string="Customer",
        required=True,
        index=True,
        ondelete="restrict",
        tracking=True,
    )
    requested_by = fields.Many2one(
        "res.users",
        default=lambda self: self.env.user,
        required=True,
        readonly=True,
        index=True,
    )
    sales_rep_partner_id = fields.Many2one(
        "res.partner",
        string="Sales Rep",
        index=True,
        readonly=True,
        copy=False,
        help=(
            "Snapshot of the acting rep's partner (taken from "
            "``requested_by.partner_id``). Used by the record rule "
            "to isolate requests between reps."
        ),
    )
    reason = fields.Text(required=True, tracking=True)
    request_type = fields.Selection(
        [
            ("field_update", "Field update"),
            ("new_child", "New contact"),
        ],
        required=True,
        default="field_update",
        tracking=True,
    )
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
            ("cancelled", "Cancelled"),
        ],
        default="pending",
        readonly=True,
        tracking=True,
        copy=False,
    )
    # Fixed fields for ``field_update`` — no more dynamic field picker.
    # Prefilled from partner_id via onchange so the rep can see the
    # current value and only change what needs changing. See
    # ``_FIELD_UPDATE_MAP`` for the form↔partner field mapping.
    new_name = fields.Char(string="New name")
    new_phone = fields.Char(string="New phone")
    new_mobile = fields.Char(string="New mobile")
    new_email = fields.Char(string="New email")
    new_street = fields.Char(string="New street")
    new_street2 = fields.Char(string="New street 2")
    new_city = fields.Char(string="New city")
    new_zip = fields.Char(string="New ZIP")
    new_state_id = fields.Many2one("res.country.state", string="New state")
    new_country_id = fields.Many2one("res.country", string="New country")

    # Per-field "changed" flags used in the form attrs to hide fields
    # that match the partner's current value after the request is
    # saved. During editing (id is False) everything shows so the rep
    # can type over any pre-filled value; once saved, the view
    # collapses to only the fields the rep actually changed — makes
    # the manager's review focus on the diff.
    new_name_changed = fields.Boolean(
        compute="_compute_field_update_changed", compute_sudo=True
    )
    new_phone_changed = fields.Boolean(
        compute="_compute_field_update_changed", compute_sudo=True
    )
    new_mobile_changed = fields.Boolean(
        compute="_compute_field_update_changed", compute_sudo=True
    )
    new_email_changed = fields.Boolean(
        compute="_compute_field_update_changed", compute_sudo=True
    )
    new_street_changed = fields.Boolean(
        compute="_compute_field_update_changed", compute_sudo=True
    )
    new_street2_changed = fields.Boolean(
        compute="_compute_field_update_changed", compute_sudo=True
    )
    new_city_changed = fields.Boolean(
        compute="_compute_field_update_changed", compute_sudo=True
    )
    new_zip_changed = fields.Boolean(
        compute="_compute_field_update_changed", compute_sudo=True
    )
    new_state_id_changed = fields.Boolean(
        compute="_compute_field_update_changed", compute_sudo=True
    )
    new_country_id_changed = fields.Boolean(
        compute="_compute_field_update_changed", compute_sudo=True
    )

    # Flag used by the form view to distinguish a rep user (who must
    # see ALL new_* fields so they can edit the record) from a
    # manager/reviewer (who sees only the fields that differ). The
    # context dependency makes the value re-evaluate per session.
    is_sales_rep_user = fields.Boolean(compute="_compute_is_sales_rep_user")

    @api.depends_context("uid")
    def _compute_is_sales_rep_user(self):
        is_rep = self.env.user.has_group(REP_GROUP_XMLID)
        for req in self:
            req.is_sales_rep_user = is_rep

    @staticmethod
    def _normalize_cmp_value(value):
        """Normalize a field value for change comparison so records /
        falsy / plain values compare consistently. Used by the
        per-field ``_changed`` compute and by ``_apply_field_update``
        to prevent drift between the two.
        """
        if hasattr(value, "id"):
            return value.id or False
        return value or False

    def _is_field_update_changed(self, form_field, model_field):
        self.ensure_one()
        partner = self.partner_id
        new_val = self[form_field]
        current = partner[model_field] if partner else False
        return self._normalize_cmp_value(new_val) != self._normalize_cmp_value(current)

    @api.depends(
        "partner_id",
        "new_name",
        "new_phone",
        "new_mobile",
        "new_email",
        "new_street",
        "new_street2",
        "new_city",
        "new_zip",
        "new_state_id",
        "new_country_id",
    )
    def _compute_field_update_changed(self):
        for req in self:
            for form_field, model_field in _FIELD_UPDATE_MAP.items():
                req[f"{form_field}_changed"] = req._is_field_update_changed(
                    form_field, model_field
                )

    new_child_name = fields.Char()
    new_child_email = fields.Char()
    new_child_phone = fields.Char()
    new_child_mobile = fields.Char()
    new_child_function = fields.Char()
    new_child_type = fields.Selection(
        [
            ("contact", "Contact"),
            ("invoice", "Invoice Address"),
            ("delivery", "Delivery Address"),
            ("other", "Other"),
        ],
        default="contact",
    )
    processed_partner_id = fields.Many2one(
        "res.partner",
        string="Created Contact",
        readonly=True,
        copy=False,
        help="For approved new_child requests: the contact created.",
    )

    @api.depends("partner_id")
    def _compute_name(self):
        for req in self:
            partner = req.partner_id.display_name or _("(unknown)")
            if req.id:
                req.name = "CR-%s · %s" % (req.id, partner)
            else:
                req.name = _("New change request · %(partner)s") % {"partner": partner}

    @api.onchange("partner_id")
    def _onchange_partner_id_prefill(self):
        """Pre-fill the ``new_*`` fields with the partner's current
        values so the rep sees what's there and only types over what
        needs changing. Only applies to field_update requests; for
        new_child the form starts empty."""
        for req in self:
            if req.request_type != "field_update" or not req.partner_id:
                continue
            for form_field, model_field in _FIELD_UPDATE_MAP.items():
                req[form_field] = req.partner_id[model_field]

    @api.onchange("request_type")
    def _onchange_request_type_reset(self):
        """Clear the opposite payload when the request type flips so
        the constraint doesn't fire on stale fields and the view
        stays tidy."""
        for req in self:
            if req.request_type == "field_update":
                for f in _NEW_CHILD_FIELDS:
                    req[f] = False
                req._onchange_partner_id_prefill()
            elif req.request_type == "new_child":
                for f in _FIELD_UPDATE_FORM_FIELDS:
                    req[f] = False

    @api.model_create_multi
    def create(self, vals_list):
        # Forbid rep-supplied values for requested_by /
        # sales_rep_partner_id: otherwise a rep could falsify the
        # author or the snapshot via RPC, breaking the audit trail
        # and the record rule that isolates requests between reps.
        is_rep = self.env.user.has_group(REP_GROUP_XMLID)
        for vals in vals_list:
            if is_rep:
                vals["requested_by"] = self.env.uid
                vals["sales_rep_partner_id"] = self.env.user.partner_id.id
            elif "sales_rep_partner_id" not in vals:
                user_id = vals.get("requested_by") or self.env.uid
                user = self.env["res.users"].browse(user_id)
                if user.has_group(REP_GROUP_XMLID):
                    vals["sales_rep_partner_id"] = user.partner_id.id
            # Prefill new_* fields from the partner for field_update
            # requests so RPC-created records that only set the
            # fields the caller wants to change do not end up
            # clearing every other field on approve. The onchange
            # covers the UI; this covers the RPC path.
            if vals.get("request_type", "field_update") == "field_update" and vals.get(
                "partner_id"
            ):
                partner = self.env["res.partner"].browse(vals["partner_id"])
                for form_field, model_field in _FIELD_UPDATE_MAP.items():
                    if form_field in vals:
                        continue
                    current = partner[model_field]
                    vals[form_field] = current.id if hasattr(current, "id") else current
        records = super().create(vals_list)
        records.filtered(
            lambda r: r.state == "pending" and r.sales_rep_partner_id
        ).request_validation()
        return records

    def write(self, vals):
        touched_audit = set(vals) & IMMUTABLE_AUDIT_FIELDS
        if touched_audit:
            raise AccessError(
                _(
                    "Cannot modify audit fields %(fields)s on a change request"
                    " — they are set at create and pin the record to its author and scope."
                )
                % {"fields": ", ".join(sorted(touched_audit))}
            )
        if "processed_partner_id" in vals and not self.env.context.get(
            INTERNAL_CTX_KEY
        ):
            raise AccessError(
                _(
                    "Cannot modify processed_partner_id — it is "
                    "only written by action_approve when creating "
                    "a new child contact."
                )
            )
        touches_payload = set(vals) & IMMUTABLE_FIELDS_AFTER_PENDING
        if touches_payload:
            for req in self:
                if req.state != "pending":
                    raise AccessError(
                        _(
                            "Cannot change fields %(fields)s on a change request"
                            " that is not in the pending state (current state: %(state)s)."
                        )
                        % {
                            "fields": ", ".join(sorted(touches_payload)),
                            "state": req.state,
                        }
                    )
        if "state" in vals and not self.env.context.get(INTERNAL_CTX_KEY):
            # A direct RPC ``write({'state': ...})`` would bypass the
            # manager guard and the tier/review plumbing in the
            # action_* handlers. Only those handlers set the
            # private context flag; anything else is an attempt to
            # short-circuit the workflow and is rejected.
            for req in self:
                if req.state != vals["state"]:
                    raise AccessError(
                        _(
                            "State transitions on a change request "
                            "are only allowed via action_approve / "
                            "action_reject / action_cancel."
                        )
                    )
        return super().write(vals)

    def unlink(self):  # pylint: disable=no-raise-unlink
        manager = self.env.user.has_group(MANAGER_GROUP_XMLID)
        for req in self:
            if not manager:
                raise AccessError(_("Only Sales Managers can delete change requests."))
            if req.state not in ("rejected", "cancelled"):
                raise AccessError(
                    _("Only rejected or cancelled change requests can be deleted.")
                )
        return super().unlink()

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    @api.constrains("request_type", "new_child_name")
    def _check_payload_matches_type(self):
        for req in self:
            if req.request_type == "field_update":
                new_child_populated = any(
                    getattr(req, f) for f in _NEW_CHILD_FIELDS if f != "new_child_type"
                )
                if new_child_populated:
                    raise ValidationError(
                        _(
                            "A field update request cannot carry "
                            "new contact data; use a new_child "
                            "request for that."
                        )
                    )
            elif req.request_type == "new_child":
                if not req.new_child_name:
                    raise ValidationError(
                        _("A new contact request must include the contact's name.")
                    )

    def _auto_init(self):
        result = super()._auto_init()
        # Partial unique index prevents two concurrent transactions from
        # committing two pending requests for the same partner, closing the
        # race condition that the Python @api.constrains cannot cover.
        self.env.cr.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            tr_partner_change_request_unique_pending_per_partner
            ON tr_partner_change_request (partner_id)
            WHERE state = 'pending'
        """
        )
        return result

    @api.constrains("state", "partner_id")
    def _check_single_pending_per_partner(self):
        for req in self:
            if req.state != "pending" or not req.partner_id:
                continue
            # ``sudo()`` is required because record rules limit the
            # acting rep to their own requests, so a pending request
            # from another rep (or from a manager) would be invisible
            # and the uniqueness check would pass incorrectly.
            duplicate = self.sudo().search_count(
                [
                    ("partner_id", "=", req.partner_id.id),
                    ("state", "=", "pending"),
                    ("id", "!=", req.id),
                ]
            )
            if duplicate:
                raise ValidationError(
                    _(
                        "There is already a pending change request for customer %(partner)s."
                        " Cancel it first if you want to open a new one."
                    )
                    % {"partner": req.partner_id.display_name}
                )

    # ------------------------------------------------------------------
    # Workflow actions
    # ------------------------------------------------------------------

    def action_cancel(self):
        # Scoping is enforced by the per-group record rule: a rep
        # only sees (and therefore can act on) their own requests,
        # while a manager sees all. An extra Python guard would
        # duplicate that and be untestable without widening the
        # rule.
        for req in self:
            if req.state != "pending":
                raise UserError(
                    _(
                        "Only pending change requests can be cancelled (current state: %(state)s)."
                    )
                    % {"state": req.state}
                )
            req._close_pending_reviews()
            req.with_context(**{INTERNAL_CTX_KEY: True}).write({"state": "cancelled"})
        return True

    def action_reject(self):
        self._ensure_manager()
        for req in self:
            if req.state != "pending":
                raise UserError(
                    _(
                        "Only pending change requests can be rejected (current state: %(state)s)."
                    )
                    % {"state": req.state}
                )
            req._raise_if_comment_required()
            # ``reject_tier`` only rejects reviews assigned to the
            # acting user. With more than one tier.definition, the
            # reviews from other steps would stay pending after the
            # request transitions to ``rejected`` and remain
            # dangling. ``_close_pending_reviews`` sweeps them
            # explicitly to keep the audit trail consistent.
            req.reject_tier()
            req._close_pending_reviews()
            req.with_context(
                **{INTERNAL_CTX_KEY: True, "skip_validation_check": True}
            ).write({"state": "rejected"})
        return True

    def action_approve(self):
        self._ensure_manager()
        for req in self:
            if req.state != "pending":
                raise UserError(
                    _(
                        "Only pending change requests can be approved (current state: %(state)s)."
                    )
                    % {"state": req.state}
                )
            req._raise_if_comment_required()
            req.validate_tier()
            if req.validation_status != "validated":
                raise UserError(
                    _(
                        "This change request still needs validation "
                        "from other reviewers."
                    )
                )
            if req.request_type == "field_update":
                req._apply_field_update()
            else:
                req._create_child()
            req.with_context(**{INTERNAL_CTX_KEY: True}).write({"state": "approved"})
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_manager(self):
        if not self.env.user.has_group(MANAGER_GROUP_XMLID):
            raise AccessError(_("Only Sales Managers can perform this action."))

    def _close_pending_reviews(self):
        """Force-close any tier reviews still in ``pending``.

        The mixin's ``reject_tier`` only rejects reviews assigned
        to the acting user; when the request has more than one
        tier definition, reviews from other steps would stay
        dangling when the request transitions to a terminal
        state. This sweep uses ``sudo`` because the acting user
        may not own all the reviews.
        """
        self.ensure_one()
        pending = self.review_ids.filtered(lambda r: r.status == "pending")
        if pending:
            # done_by=False is intentional: these reviews are force-closed by a
            # state transition (cancel/reject), not by a reviewer decision.
            # Assigning env.user.id here would make the acting user appear as
            # the reviewer of tiers they were never assigned to.
            pending.sudo().write({"status": "rejected", "done_by": False})

    def _raise_if_comment_required(self):
        self.ensure_one()
        if self.review_ids.definition_id.filtered("has_comment"):
            raise UserError(
                _(
                    "This model does not support tier definitions "
                    "with has_comment=True. Uncheck it on the "
                    "tier.definition or extend action_approve / "
                    "action_reject to propagate the wizard action "
                    "returned by the mixin."
                )
            )

    def _apply_field_update(self):
        """Write only the fields whose ``new_*`` value differs from
        the partner's current value. ``False`` is applied too, so the
        rep can clear an existing value (e.g. remove an outdated
        phone). Comparison is delegated to ``_is_field_update_changed``
        so the form view and the apply logic never drift apart."""
        self.ensure_one()
        vals = {}
        for form_field, model_field in _FIELD_UPDATE_MAP.items():
            if not self._is_field_update_changed(form_field, model_field):
                continue
            new_val = self[form_field]
            vals[model_field] = self._normalize_cmp_value(new_val) or False
        if vals:
            self.partner_id.sudo().write(vals)

    def _create_child(self):
        self.ensure_one()
        child_vals = {
            "name": self.new_child_name,
            "parent_id": self.partner_id.id,
            "type": self.new_child_type,
        }
        for src, dst in (
            ("new_child_email", "email"),
            ("new_child_phone", "phone"),
            ("new_child_mobile", "mobile"),
            ("new_child_function", "function"),
        ):
            value = getattr(self, src)
            if value:
                child_vals[dst] = value
        child = self.env["res.partner"].sudo().create(child_vals)
        # ``skip_validation_check`` bypasses the mixin's "write under
        # validation" guard (tier_validation.py:394) — needed because
        # ``processed_partner_id`` is written while the record is
        # still in ``pending`` (state flips to ``approved`` right
        # after, in ``action_approve``). ``INTERNAL_CTX_KEY`` is
        # required by the module's own write guard which keeps
        # ``processed_partner_id`` read-only from RPC.
        self.with_context(
            **{INTERNAL_CTX_KEY: True, "skip_validation_check": True}
        ).write({"processed_partner_id": child.id})

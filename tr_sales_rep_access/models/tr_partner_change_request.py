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

IMMUTABLE_FIELDS_AFTER_PENDING = frozenset(
    {
        "partner_id",
        "request_type",
        "reason",
        "line_ids",
        "new_child_name",
        "new_child_email",
        "new_child_phone",
        "new_child_mobile",
        "new_child_function",
        "new_child_type",
    }
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
    line_ids = fields.One2many(
        "tr.partner.change.request.line",
        "request_id",
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
                req.name = _("New change request · %s") % partner

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
                    "Cannot modify audit fields %(fields)s on a "
                    "change request — they are set at create and "
                    "pin the record to its author and scope."
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
                            "Cannot change fields %(fields)s on a "
                            "change request that is not in the "
                            "pending state (current state: "
                            "%(state)s)."
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

    def unlink(self):
        manager = self.env.user.has_group(MANAGER_GROUP_XMLID)
        for req in self:
            if not manager:
                raise AccessError(_("Only Sales Managers can delete change requests."))
            if req.state not in ("rejected", "cancelled"):
                raise AccessError(
                    _("Only rejected or cancelled change requests " "can be deleted.")
                )
        return super().unlink()

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    @api.constrains(
        "request_type",
        "line_ids",
        "new_child_name",
        "new_child_email",
        "new_child_phone",
        "new_child_mobile",
        "new_child_function",
    )
    def _check_payload_matches_type(self):
        for req in self:
            if req.request_type == "field_update":
                if not req.line_ids:
                    raise ValidationError(
                        _(
                            "A field update request must have at "
                            "least one line describing the change."
                        )
                    )
                if any(
                    getattr(req, f)
                    for f in (
                        "new_child_name",
                        "new_child_email",
                        "new_child_phone",
                        "new_child_mobile",
                        "new_child_function",
                    )
                ):
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
                        _("A new contact request must include " "the contact's name.")
                    )
                if req.line_ids:
                    raise ValidationError(
                        _(
                            "A new contact request cannot carry "
                            "field update lines; use a field_update "
                            "request for that."
                        )
                    )

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
                        "There is already a pending change request "
                        "for customer %s. Cancel it first if you "
                        "want to open a new one."
                    )
                    % req.partner_id.display_name
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
                        "Only pending change requests can be "
                        "cancelled (current state: %s)."
                    )
                    % req.state
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
                        "Only pending change requests can be "
                        "rejected (current state: %s)."
                    )
                    % req.state
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
                        "Only pending change requests can be "
                        "approved (current state: %s)."
                    )
                    % req.state
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
            pending.sudo().write({"status": "rejected", "done_by": self.env.user.id})

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
        self.ensure_one()
        vals = {}
        for line in self.line_ids:
            vals[line.field_id.name] = line._resolve_value()
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

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

REP_GROUP_XMLID = "tr_sales_rep_access.group_sales_rep_external"
_GROUPS_NO_REP = "!tr_sales_rep_access.group_sales_rep_external"

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    sales_rep_partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Sales Rep",
        domain=[("agent", "=", True)],
        copy=False,
        index=True,
        readonly=True,
        help=(
            "Commercial agent responsible for this order, captured at "
            "creation from the customer's agent_ids. Re-synced if the "
            "customer changes while the order is in draft. Immutable "
            "after draft."
        ),
    )
    tr_rep_notes = fields.Text(
        string="Sales Rep Notes",
        help=(
            "Operational notes from the sales representative about this "
            "order. Not printed on the PDF; intended for internal "
            "coordination with the sales manager."
        ),
    )
    is_sales_rep_external = fields.Boolean(
        compute="_compute_is_sales_rep_external",
    )
    tr_rep_conference_required = fields.Boolean(
        string="Needs Rep Conference",
        default=False,
        copy=False,
        index=True,
        help=(
            "True when the order was created by an external sales rep "
            "and must pass the ``Sales rep order conference`` tier "
            "before it can be confirmed. Admin / manager orders leave "
            "this flag False so ``base_tier_validation`` does not "
            "block their legitimate writes on confirm. Set by the "
            "``create()`` override when the caller has the rep group; "
            "never written by hand."
        ),
    )

    def _compute_is_sales_rep_external(self):
        is_rep = self.env.user.has_group(REP_GROUP_XMLID)
        for record in self:
            record.is_sales_rep_external = is_rep

    # PR 8 / PR 12 — chatter hidden for reps.  field-level groups= closes the
    # RPC read path (fields_get / read) without touching mail.message
    # ACLs globally (which would break mail.activity creation).
    # PR 12 extends the block to all auxiliary mail.thread fields so that
    # metadata (follower list, attachment count, needaction counters) is
    # also invisible via direct RPC read / fields_get.
    # Stock delivery fields — hidden for reps (no ACL on stock.picking).
    # groups= removes these from the arch for reps so the dependent computes
    # (_compute_picking_ids, _compute_effective_date, _compute_delivery_status,
    # _compute_json_popover) never run under a rep session.
    picking_ids = fields.One2many(groups=_GROUPS_NO_REP)
    delivery_count = fields.Integer(groups=_GROUPS_NO_REP)
    effective_date = fields.Datetime(groups=_GROUPS_NO_REP)
    delivery_status = fields.Selection(groups=_GROUPS_NO_REP)
    json_popover = fields.Char(groups=_GROUPS_NO_REP)
    show_json_popover = fields.Boolean(groups=_GROUPS_NO_REP)

    # Commission fields — hidden for reps (no ACL on sale.order.line.agent).
    # The computed fields read agent_ids, which would trigger AccessError.
    commission_total = fields.Float(groups=_GROUPS_NO_REP)
    partner_agent_ids = fields.Many2many(groups=_GROUPS_NO_REP)
    # unmanaged_commission_warning (tr_commercial_policy) iterates
    # order_line.agent_ids — its compute raises AccessError for reps
    # because agent_ids is itself blocked for them. Cosmetic warning the
    # rep doesn't need to see anyway.
    unmanaged_commission_warning = fields.Char(groups=_GROUPS_NO_REP)

    # Advance payment link (sale_advance_payment). Reps have no ACL on
    # account.payment; the ORM filters the One2many IDs via that ACL
    # and raises when the rep tries to read the parent order.
    account_payment_ids = fields.One2many(groups=_GROUPS_NO_REP)

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
    website_message_ids = fields.One2many(groups=_GROUPS_NO_REP)

    def _get_view(self, view_id=None, view_type="form", **options):
        arch, view = super()._get_view(view_id=view_id, view_type=view_type, **options)
        if view_type != "form" or not self.env.user.has_group(REP_GROUP_XMLID):
            return arch, view
        for node in arch.xpath("//page[@name='other_information']"):
            modifiers = json.loads(node.get("modifiers") or "{}")
            modifiers["invisible"] = True
            node.set("modifiers", json.dumps(modifiers))
            node.set("invisible", "1")
        # Invoices smart button — hidden for reps. Groups= on the button
        # would remove invoice_count from the arch (the field lives
        # inside the button) and break attrs on another button that
        # references it. Modifiers on the button node keep the field
        # reachable and just hide the UI.
        for node in arch.xpath("//button[@name='action_view_invoice']"):
            modifiers = json.loads(node.get("modifiers") or "{}")
            modifiers["invisible"] = True
            node.set("modifiers", json.dumps(modifiers))
            node.set("invisible", "1")
        # Fiscal operation / operation line / CFOP / description are
        # defined by the backoffice. Reps can see them but must not edit.
        # Inject readonly modifier directly on the arch nodes inside
        # the order_line inline form.
        #
        # ``force_save="1"`` is required: Odoo's web client strips
        # readonly fields from the vals on save, which on new lines
        # would discard the ``default_fiscal_operation_id`` /
        # ``default_partner_id`` / ``default_company_id`` carried by
        # the order_line widget context. Without force_save, multi-
        # line orders saved by reps end up with empty fiscal_operation_id
        # on every new line, breaking _compute_fiscal_operation_line_id
        # / cfop_id / amount_tax / total_financeiro downstream.
        readonly_line_fields = (
            "fiscal_operation_id",
            "fiscal_operation_line_id",
            "cfop_id",
            "name",
            "product_uom",
        )
        for fname in readonly_line_fields:
            for node in arch.xpath(
                f"//field[@name='order_line']//field[@name='{fname}']"
            ):
                modifiers = json.loads(node.get("modifiers") or "{}")
                modifiers["readonly"] = True
                node.set("modifiers", json.dumps(modifiers))
                node.set("readonly", "1")
                node.set("force_save", "1")
        # Header fields defined by the backoffice: rep can see but not edit.
        # ``force_save="1"`` for the same reason as the line readonly block
        # above — keep the value in the save vals despite the readonly modifier.
        readonly_header_fields = ("carrier_id",)
        for fname in readonly_header_fields:
            for node in arch.xpath(
                f"//sheet//field[@name='{fname}'][not(ancestor::field[@name='order_line'])]"
            ):
                modifiers = json.loads(node.get("modifiers") or "{}")
                modifiers["readonly"] = True
                node.set("modifiers", json.dumps(modifiers))
                node.set("readonly", "1")
                node.set("force_save", "1")
        return arch, view

    @api.model_create_multi
    def create(self, vals_list):
        is_rep = self.env.user.has_group(REP_GROUP_XMLID)
        for vals in vals_list:
            self._sales_rep_prepare_create_vals(vals)
            if is_rep:
                # PR 6b — rep-created orders must carry the conference
                # flag so the tier definition's domain fires for them
                # only. Force True here (not ``setdefault``) so a rep
                # cannot bypass the tier by passing
                # ``tr_rep_conference_required=False`` in the vals
                # via RPC/import. Admin / manager / imports keep
                # their explicit value (defaults to False), which
                # keeps ``base_tier_validation`` from freezing
                # legitimate internal writes.
                vals["tr_rep_conference_required"] = True
        records = super().create(vals_list)
        # PR 6b — ``notify_on_create`` on the tier definition does
        # nothing on its own; reviews only appear after an explicit
        # ``request_validation()`` call (same pattern as PR 3's
        # partner draft flow). Kept without ``sudo()`` so
        # ``tier.review.requested_by`` points at the acting rep,
        # preserving the audit trail — the base ACL on
        # ``tier.review`` is global (see
        # ``base_tier_validation/security/ir.model.access.csv``),
        # so the rep can create reviews without needing sudo.
        if is_rep:
            rep_orders = records.filtered(lambda o: o.tr_rep_conference_required)
            if rep_orders:
                rep_orders.request_validation()
        return records

    def write(self, vals):
        self._sales_rep_prepare_write_vals(vals)
        self._sales_rep_check_rep_can_edit(vals)
        if self.env.user.has_group(REP_GROUP_XMLID):
            # PR 8 — suppress tracking messages when the rep writes so
            # that internal chatter is not polluted.  Trade-off: rep
            # writes produce no audit trail in the chatter (documented
            # in AGENTS.md "PR 8 mail_notrack trade-off").
            return super(SaleOrder, self.with_context(mail_notrack=True)).write(vals)
        return super().write(vals)

    def action_confirm(self):
        # PR 3 — a customer that was Active at order-create time
        # but was demoted to Draft (or Inactive) before confirm
        # would otherwise slip past the @api.constrains below,
        # which only fires on partner_id change. Core
        # sale.order.action_confirm only writes the order state,
        # so we guard here explicitly. We check the commercial
        # partner so a child contact of a Draft customer cannot
        # be used as a backdoor.
        for order in self:
            commercial = order.partner_id.commercial_partner_id
            if commercial.state != "confirmed":
                raise ValidationError(
                    _(
                        "Cannot confirm order %(order)s: customer "
                        "%(customer)s is not active."
                    )
                    % {
                        "order": order.display_name,
                        "customer": commercial.display_name,
                    }
                )
        return super().action_confirm()

    @api.constrains("partner_id")
    def _sales_rep_check_partner_confirmed(self):
        # PR 3 — block orders whose customer (or customer's
        # commercial parent) is not yet approved by the tier
        # workflow. The commercial-partner check prevents a rep
        # from creating an Active child contact under a Draft
        # customer and using the child to sell.
        for order in self:
            commercial = order.partner_id.commercial_partner_id
            if commercial and commercial.state != "confirmed":
                raise ValidationError(
                    _(
                        "Customer %s is not active yet. It must be "
                        "approved before placing orders."
                    )
                    % commercial.display_name
                )

    def _sales_rep_check_rep_can_edit(self, vals):
        """Block rep users from editing orders past draft.

        State-based record rule was avoided to let ``sale_stock`` perform
        legitimate internal writes (e.g. ``procurement_group_id``) on
        confirmed orders; those writes never run under a rep user.
        """
        if not self.env.user.has_group(REP_GROUP_XMLID):
            return
        non_drafts = self.filtered(lambda o: o.state != "draft")
        if non_drafts:
            raise AccessError(
                _(
                    "Sales reps can only modify quotations in draft. "
                    "Order(s) %s are past draft."
                )
                % ", ".join(non_drafts.mapped("display_name"))
            )

    @api.model
    def _sales_rep_prepare_create_vals(self, vals):
        """Populate or validate sales_rep_partner_id in create vals."""
        partner = self._sales_rep_browse_partner(vals.get("partner_id"))
        if not partner:
            return
        expected = self._sales_rep_resolve(partner)
        provided = vals.get("sales_rep_partner_id")
        if provided:
            if (expected or False) != provided:
                self._sales_rep_raise_divergence(partner, provided)
        else:
            vals["sales_rep_partner_id"] = expected

    def _sales_rep_prepare_write_vals(self, vals):
        """Sync or validate sales_rep_partner_id on write.

        Covers both direct change of the snapshot and indirect change via
        ``partner_id``. Any mutation of a non-draft order's customer or
        snapshot raises UserError. In draft, the snapshot is re-synced
        from the new customer. Always enforced before mutating vals.
        """
        partner_changed = "partner_id" in vals
        snapshot_changed = "sales_rep_partner_id" in vals

        if partner_changed or snapshot_changed:
            self._sales_rep_check_immutability(vals)

        if partner_changed:
            new_partner = self._sales_rep_browse_partner(vals["partner_id"])
            new_expected = (
                self._sales_rep_resolve(new_partner) if new_partner else False
            )
            if snapshot_changed:
                if (new_expected or False) != (
                    vals.get("sales_rep_partner_id") or False
                ):
                    self._sales_rep_raise_divergence(
                        new_partner, vals.get("sales_rep_partner_id")
                    )
            else:
                # All records are draft at this point (checked above);
                # safe to sync the shared vals for the whole recordset.
                vals["sales_rep_partner_id"] = new_expected
        elif snapshot_changed:
            # Explicit snapshot change without partner change:
            # must match each record's current partner.
            provided = vals.get("sales_rep_partner_id") or False
            for order in self:
                expected = (
                    self._sales_rep_resolve(order.partner_id)
                    if order.partner_id
                    else False
                )
                if (expected or False) != provided:
                    self._sales_rep_raise_divergence(order.partner_id, provided)

    def _sales_rep_check_immutability(self, vals):
        """Block customer or snapshot change once order is past draft.

        Both ``partner_id`` and ``sales_rep_partner_id`` are immutable
        after draft; indirect snapshot change via customer switch is
        blocked here too.
        """
        non_drafts = self.filtered(lambda o: o.state != "draft")
        if not non_drafts:
            return
        field_label = (
            _("Sales Rep") if "sales_rep_partner_id" in vals else _("Customer")
        )
        raise UserError(
            _("Cannot change %(field)s on %(orders)s: order is no longer " "in draft.")
            % {
                "field": field_label,
                "orders": ", ".join(non_drafts.mapped("display_name")),
            }
        )

    @api.model
    def _sales_rep_browse_partner(self, partner_id):
        if not partner_id:
            return self.env["res.partner"].browse()
        return self.env["res.partner"].browse(partner_id)

    @api.model
    def _sales_rep_resolve(self, partner):
        """Return the agent id from partner.commercial_partner_id.agent_ids.

        tr_commercial_policy constrains agent_ids to length <= 1. If for any
        reason the constraint is bypassed and there are 2+ agents, log a
        warning and return False to avoid leaking an arbitrary choice.
        """
        if not partner:
            return False
        commercial = partner.commercial_partner_id or partner
        agents = commercial.agent_ids
        if len(agents) == 1:
            return agents.id
        if len(agents) > 1:
            _logger.warning(
                "Customer %s has more than one agent; sales_rep_partner_id "
                "cannot be auto-resolved.",
                commercial.display_name,
            )
        return False

    def _sales_rep_raise_divergence(self, partner, provided_id):
        partner_label = partner.display_name if partner else _("(unknown)")
        if provided_id:
            rep_label = self.env["res.partner"].browse(provided_id).display_name
        else:
            rep_label = _("(empty)")
        raise ValidationError(
            _(
                "Sales Rep %(rep)s does not match the commercial agent of "
                "customer %(customer)s."
            )
            % {"rep": rep_label, "customer": partner_label}
        )

    def _prepare_invoice(self):
        vals = super()._prepare_invoice()
        vals["sales_rep_partner_id"] = self.sales_rep_partner_id.id
        return vals

    def _get_invoice_grouping_keys(self):
        res = super()._get_invoice_grouping_keys()
        if "sales_rep_partner_id" not in res:
            res.append("sales_rep_partner_id")
        return res

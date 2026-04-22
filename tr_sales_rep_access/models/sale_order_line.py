# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

REP_GROUP_XMLID = "tr_sales_rep_access.group_sales_rep_external"
_GROUPS_NO_REP = "!tr_sales_rep_access.group_sales_rep_external"


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    # PR 9 — stock availability fields hidden for reps (field-level RPC block).
    # These are computed by sale_stock and fed to the qty_at_date_widget.
    # display_qty_widget controls the widget's OWL template visibility:
    # blocking it (undefined on frontend) keeps the icon invisible even when
    # the other qty fields are also absent.
    display_qty_widget = fields.Boolean(groups=_GROUPS_NO_REP)
    virtual_available_at_date = fields.Float(groups=_GROUPS_NO_REP)
    qty_available_today = fields.Float(groups=_GROUPS_NO_REP)
    free_qty_today = fields.Float(groups=_GROUPS_NO_REP)
    forecast_expected_date = fields.Datetime(groups=_GROUPS_NO_REP)
    scheduled_date = fields.Datetime(groups=_GROUPS_NO_REP)
    qty_to_deliver = fields.Float(groups=_GROUPS_NO_REP)

    # Additional stock-linked fields whose compute reads stock.move
    # records. Without these, line create/read triggers AccessError on
    # stock.move for reps (no ACL on that model).
    move_ids = fields.One2many(groups=_GROUPS_NO_REP)
    is_mto = fields.Boolean(groups=_GROUPS_NO_REP)
    qty_delivered = fields.Float(groups=_GROUPS_NO_REP)
    qty_delivered_method = fields.Selection(groups=_GROUPS_NO_REP)

    # Commission fields hidden for reps at model level. agent_ids itself
    # is blocked because the ORM read of a One2many checks the child model's
    # ACL to filter IDs; without it the rep's client hits AccessError when
    # reading sale.order. The compute and resolver below sudo around this
    # block so the legitimate internal write path still runs. commission_free
    # intentionally NOT blocked: it feeds _compute_agent_ids server-side.
    agent_ids = fields.One2many(groups=_GROUPS_NO_REP)
    commission_status = fields.Char(groups=_GROUPS_NO_REP)

    # Analytic accounting detail — reps have no ACL on account.analytic.line
    # and the data is not relevant for order entry. Hiding the O2m removes
    # the only path the form has to reach analytic lines.
    analytic_line_ids = fields.One2many(groups=_GROUPS_NO_REP)

    # Incoming purchase lines — One2many to purchase.order.line (no rep ACL).
    # Same pattern as the other child-model fields above.
    purchase_line_ids = fields.One2many(groups=_GROUPS_NO_REP)

    # product_updatable (sale_stock) iterates move_ids in its compute —
    # reps can't read move_ids, so the field would raise AccessError
    # when the client reads the line. Reps only edit draft orders
    # where product is always updatable anyway.
    product_updatable = fields.Boolean(groups=_GROUPS_NO_REP)

    @api.depends("order_id.partner_id")
    def _compute_agent_ids(self):
        """Run the agent-line compute as sudo for reps.

        The rep has no ACL on ``sale.order.line.agent`` — giving direct
        CRUD would open the model to RPC mutation and weaken the
        post-confirm guard and band-rate enforcement. Sudo here lets
        the legitimate system compute create/unlink child records
        without handing the rep a usable write surface.
        """
        if self.env.user.has_group(REP_GROUP_XMLID):
            return super(SaleOrderLine, self.sudo())._compute_agent_ids()
        return super()._compute_agent_ids()

    def _resolve_agent_commissions(self):
        """Same rationale as ``_compute_agent_ids``: the rep must not
        write to ``sale.order.line.agent`` via RPC, but the policy
        resolver (called from core line create/write) must run to
        keep ``commission_id`` pointing at the band's managed record.
        """
        if self.env.user.has_group(REP_GROUP_XMLID):
            return super(SaleOrderLine, self.sudo())._resolve_agent_commissions()
        return super()._resolve_agent_commissions()

    @api.model_create_multi
    def create(self, vals_list):
        # Check before super() so the guard fires before Odoo core calls
        # message_post on the parent order (which fails in test environments
        # without email config and would mask our AccessError).
        # order_id is required on sale.order.line so it is always present.
        if self.env.user.has_group(REP_GROUP_XMLID):
            non_drafts = (
                self.env["sale.order"]
                .browse({v["order_id"] for v in vals_list})
                .filtered(lambda o: o.state != "draft")
            )
            if non_drafts:
                raise AccessError(
                    _(
                        "Sales reps can only add lines to quotations in draft. "
                        "Order(s) %(orders)s are past draft."
                    )
                    % {"orders": ", ".join(non_drafts.mapped("display_name"))}
                )
        return super().create(vals_list)

    def write(self, vals):
        self._sales_rep_check_rep_can_edit()
        return super().write(vals)

    def unlink(self):
        self._sales_rep_check_rep_can_edit()
        return super().unlink()

    @api.constrains("product_id")
    def _sales_rep_check_product_in_catalog(self):
        """Block rep users from lining products outside their catalog.

        Complements product.[template|product]._search() which filters
        listings: the constraint catches RPC / import / load() paths
        that bypass search by passing product_id directly.
        """
        if not self.env.user.has_group(REP_GROUP_XMLID):
            return
        visible = self.env.user.partner_id.sudo()._get_visible_category_ids()
        if not visible:
            offenders = self.filtered(lambda line: line.product_id)
        else:
            offenders = self.filtered(
                lambda line: line.product_id and line.product_id.categ_id not in visible
            )
        if offenders:
            raise ValidationError(
                _("Product(s) %(products)s are outside your assigned catalog.")
                % {
                    "products": ", ".join(
                        offenders.product_id.sudo().mapped("display_name")
                    )
                }
            )

    def _sales_rep_check_rep_can_edit(self):
        """Block rep users from touching lines whose order is past draft.

        Uses a Python override (not a state-based record rule) because
        ``sale_stock`` performs internal writes on confirmed order lines
        (propagation of ``product_uom_qty`` to stock moves, etc.) that
        must still work when a non-rep user drives the flow. The rep
        group never executes those code paths.
        """
        if not self.env.user.has_group(REP_GROUP_XMLID):
            return
        non_drafts = self.filtered(lambda line: line.order_id.state != "draft")
        if non_drafts:
            raise AccessError(
                _(
                    "Sales reps can only modify quotation lines in draft. "
                    "Line(s) %(lines)s belong to an order past draft."
                )
                % {"lines": ", ".join(non_drafts.mapped("display_name"))}
            )

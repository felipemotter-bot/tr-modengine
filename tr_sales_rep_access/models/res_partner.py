# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

MANAGER_GROUP_XMLID = "tr_commercial_policy.group_sales_manager"
REP_GROUP_XMLID = "tr_sales_rep_access.group_sales_rep_external"
DEFAULT_CATALOG_PARAM = "tr_sales_rep_access.tr_sales_rep_default_category_ids"
DRAFT_STAGE_XMLID = "partner_stage.partner_stage_draft"


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
            # PR 3 — auto-populate the acting rep as the customer's
            # agent when missing. Required because the stack in
            # this project does not install
            # sale_commission_agent_restrict, so nothing else would
            # set agent_ids and the tier_definition (which demands
            # agent_ids != False) would never fire — leaving the
            # customer stuck in Draft forever. Only applies to new
            # commercial partners; child contacts inherit access
            # through the commercial partner and do not need their
            # own agent.
            if (
                is_rep
                and rep_partner_id
                and not vals.get("parent_id")
                and "agent_ids" not in vals
            ):
                vals["agent_ids"] = [(4, rep_partner_id)]
            # PR 3 — force Draft stage for new commercial partners
            # created by a rep, overriding any value explicitly
            # passed. RPC/import could otherwise send
            # ``stage_id=active`` and, combined with the
            # auto-populated ``agent_ids`` above, bypass the tier
            # workflow. Child contacts (parent_id set) are not
            # forced — protection against selling to an unapproved
            # customer lives in sale.order's guard against
            # ``commercial_partner_id.state``.
            if is_rep and draft_stage and not vals.get("parent_id"):
                vals["stage_id"] = draft_stage.id
        records = super().create(vals_list)
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

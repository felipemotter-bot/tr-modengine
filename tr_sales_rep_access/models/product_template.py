# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.osv import expression

REP_GROUP_XMLID = "tr_sales_rep_access.group_sales_rep_external"
_GROUPS_NO_REP = "!tr_sales_rep_access.group_sales_rep_external"


class ProductTemplate(models.Model):
    _inherit = "product.template"

    # PR 9 — stock quantities hidden for reps (field-level RPC block).
    qty_available = fields.Float(groups=_GROUPS_NO_REP)
    virtual_available = fields.Float(groups=_GROUPS_NO_REP)
    incoming_qty = fields.Float(groups=_GROUPS_NO_REP)
    outgoing_qty = fields.Float(groups=_GROUPS_NO_REP)

    def _apply_ir_rules(self, query, mode="read"):
        res = super()._apply_ir_rules(query, mode)
        if mode != "read" or self.env.su:
            return res
        _apply_rep_catalog_query(self.env, query, self._table)
        return res

    def check_access_rule(self, operation):
        # Secondary defense for non-column-field reads (the else branch of
        # _read()). For column reads, _apply_ir_rules handles the filtering.
        super().check_access_rule(operation)
        if operation != "read" or self.env.su:
            return
        if not self.env.user.has_group(REP_GROUP_XMLID):
            return
        visible = self.env.user.partner_id.sudo()._get_visible_category_ids()
        if not visible:
            if self:
                raise AccessError(_("You do not have access to any products."))
            return
        visible_ids = set(visible.ids)
        forbidden_ids = {p.id for p in self.sudo() if p.categ_id.id not in visible_ids}
        if forbidden_ids:
            names = ", ".join(
                self.env["product.template"]
                .sudo()
                .browse(list(forbidden_ids))
                .mapped("name")
            )
            raise AccessError(_("You do not have access to product(s): %s", names))

    @api.model
    def _search(
        self,
        args,
        offset=0,
        limit=None,
        order=None,
        count=False,
        access_rights_uid=None,
    ):
        args = _apply_rep_catalog_domain(self.env, args)
        return super()._search(
            args,
            offset=offset,
            limit=limit,
            order=order,
            count=count,
            access_rights_uid=access_rights_uid,
        )


def _apply_rep_catalog_domain(env, args):
    """Narrow ``args`` to the rep's visible categories.

    Returns the original ``args`` for non-rep users so other
    consumers are never affected. For rep users with no visible
    categories, returns ``[('id', '=', 0)]`` — the fail-safe
    meaning "nothing visible". Shared by product.template and
    product.product _search overrides.
    """
    if not env.user.has_group(REP_GROUP_XMLID):
        return args
    visible = env.user.partner_id.sudo()._get_visible_category_ids()
    if not visible:
        return [("id", "=", 0)]
    # ``in`` (not ``child_of``) because ``visible`` already contains every
    # descendant category minus the excluded subtrees. Using ``child_of``
    # would re-expand from the roots and pull excluded categories back in.
    return expression.AND([args or [], [("categ_id", "in", visible.ids)]])


def _apply_rep_catalog_query(env, query, categ_id_table):
    """Add a catalog WHERE clause to ``query`` for the current rep user.

    ``categ_id_table`` is the SQL table alias that owns the ``categ_id``
    column (``product_template`` for both product.template and the subquery
    used by product.product). Shared by _apply_ir_rules overrides on
    product.template and product.product.

    No-ops for non-rep users and sudo contexts — callers must guard
    with ``_apply_ir_rules``'s own ``self.env.su`` check, which already
    ran via super() before this helper is called.
    """
    if env.su or not env.user.has_group(REP_GROUP_XMLID):
        return
    visible = env.user.partner_id.sudo()._get_visible_category_ids()
    if not visible:
        query.add_where("FALSE")
        return
    query.add_where(
        f'"{categ_id_table}"."categ_id" IN %s',
        [tuple(visible.ids)],
    )

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
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

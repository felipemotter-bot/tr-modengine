# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from .product_template import (
    REP_GROUP_XMLID,
    _GROUPS_NO_REP,
    _apply_rep_catalog_domain,
)


class ProductProduct(models.Model):
    _inherit = "product.product"

    # PR 9 — stock quantities hidden for reps (mirrors product.template).
    qty_available = fields.Float(groups=_GROUPS_NO_REP)
    virtual_available = fields.Float(groups=_GROUPS_NO_REP)
    incoming_qty = fields.Float(groups=_GROUPS_NO_REP)
    outgoing_qty = fields.Float(groups=_GROUPS_NO_REP)

    def _apply_ir_rules(self, query, mode="read"):
        res = super()._apply_ir_rules(query, mode)
        if mode != "read" or self.env.su:
            return res
        if not self.env.user.has_group(REP_GROUP_XMLID):
            return res
        visible = self.env.user.partner_id.sudo()._get_visible_category_ids()
        if not visible:
            query.add_where("FALSE")
            return res
        # categ_id is on product_template (via _inherits). Filter via subquery
        # to avoid relying on whether the inherits join was added by super().
        query.add_where(
            f'"{self._table}"."product_tmpl_id" IN '
            f'(SELECT "id" FROM "product_template" WHERE "categ_id" IN %s)',
            [tuple(visible.ids)],
        )
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
                self.env["product.product"]
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

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, models
from odoo.exceptions import AccessError
from odoo.osv import expression

REP_GROUP_XMLID = "tr_sales_rep_access.group_sales_rep_external"


class ProductCategory(models.Model):
    _inherit = "product.category"

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
        if not self.env.su and self.env.user.has_group(REP_GROUP_XMLID):
            visible = self.env.user.partner_id.sudo()._get_visible_category_ids()
            if visible:
                # Restrict discovery to visible categories only — ancestors are
                # intentionally excluded from search results so the rep cannot
                # enumerate the full category tree via search/name_search.
                args = expression.AND([args or [], [("id", "in", visible.ids)]])
            else:
                args = expression.AND([args or [], [("id", "=", False)]])
        return super()._search(
            args,
            offset=offset,
            limit=limit,
            order=order,
            count=count,
            access_rights_uid=access_rights_uid,
        )

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
        # Allow visible categories and their ancestors so that reading
        # categ_id.display_name / complete_name / parent_id on a product
        # in the rep's catalog does not raise AccessError mid-traversal.
        # _search is still restricted to visible.ids only (no ancestors),
        # so ancestors are readable via direct read but not discoverable.
        ancestors = (
            self.env["product.category"]
            .sudo()
            .search([("id", "parent_of", visible.ids)])
        )
        allowed_ids = tuple(set(visible.ids) | set(ancestors.ids))
        query.add_where(f'"{self._table}"."id" IN %s', [allowed_ids])
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
                raise AccessError(
                    _("You do not have access to any product categories.")
                )
            return
        ancestors = (
            self.env["product.category"]
            .sudo()
            .search([("id", "parent_of", visible.ids)])
        )
        allowed_ids = set(visible.ids) | set(ancestors.ids)
        forbidden_ids = {c.id for c in self.sudo() if c.id not in allowed_ids}
        if forbidden_ids:
            names = ", ".join(
                self.env["product.category"]
                .sudo()
                .browse(list(forbidden_ids))
                .mapped("name")
            )
            raise AccessError(
                _("You do not have access to product category: %s", names)
            )

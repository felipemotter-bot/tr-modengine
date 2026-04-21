# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

from .product_template import _apply_rep_catalog_domain, _GROUPS_NO_REP


class ProductProduct(models.Model):
    _inherit = "product.product"

    # PR 9 — stock quantities hidden for reps (mirrors product.template).
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

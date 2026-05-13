# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models

MANAGER_GROUP_XMLID = "sales_team.group_sale_manager"


class PricelistBasicWizard(models.TransientModel):
    _inherit = "tr.pricelist.basic.wizard"

    simulate_as_agent_id = fields.Many2one(
        "res.partner",
        string="Simulate as Rep",
        domain="[('agent', '=', True)]",
        groups=MANAGER_GROUP_XMLID,
        help="Manager-only. Filter the basic pricelist to the categories "
        "visible to the chosen rep, to preview what they would see. "
        "Leave empty for the full catalog.",
    )

    def _resolve_products(self, category_ids=None, company_id=False):
        products = super()._resolve_products(
            category_ids=category_ids, company_id=company_id
        )
        # Guard before reading the restricted field: a non-manager user
        # (rep external) has no read access to ``simulate_as_agent_id``,
        # so the attribute read would either raise or silently return
        # False depending on cache state. The guard also keeps the rep's
        # own ``_search`` filter (in ``product.product``) as the single
        # source of truth for what the rep sees.
        if not self.env.user.has_group(MANAGER_GROUP_XMLID):
            return products
        if not self.simulate_as_agent_id:
            return products
        # ``allowed_category_ids`` / ``excluded_category_ids`` are field-
        # level group-restricted; a plain read by a sales manager (who is
        # not in the rep-access admin group) would yield ``AccessError``.
        # Sudo is bounded: we only consume the resulting category ids to
        # narrow products the user already has the right to see.
        visible_ids = set(
            self.simulate_as_agent_id.sudo()._get_visible_category_ids().ids
        )
        return products.filtered(lambda p: p.categ_id.id in visible_ids)

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, models
from odoo.exceptions import UserError

# States where mutating the commission agent line is still legitimate.
# Past these states the line agent structure is frozen for everyone
# except sales directors and system admins.
_EDITABLE_SALE_STATES = ("draft", "sent")


class SaleOrderLineAgent(models.Model):
    _inherit = "sale.order.line.agent"

    def _has_post_confirm_bypass(self):
        return self.env.user.has_group(
            "tr_commercial_policy.group_sales_director"
        ) or self.env.user.has_group("base.group_system")

    def _check_mutation_allowed(self):
        """Freeze the *structure* of commission agent lines once the
        order is past draft/sent.

        Does NOT validate the semantic correctness of commission_id
        (expected rate, tr_managed flag, etc.); that remains the job
        of ``_check_stale_commissions`` at confirm time. The guard is
        purely state-based: after confirmation, only a sales director
        (or system admin) can mutate the line agent.
        """
        if self._has_post_confirm_bypass():
            return
        for rec in self:
            order = rec.object_id.order_id
            if order.state in _EDITABLE_SALE_STATES:
                continue
            raise UserError(
                _(
                    "Cannot modify commission agents on order '%(order)s': "
                    "order is past draft. Only a sales director can."
                )
                % {"order": order.display_name}
            )

    @api.model_create_multi
    def create(self, vals_list):
        if not self._has_post_confirm_bypass():
            line_ids = [
                vals.get("object_id") for vals in vals_list if vals.get("object_id")
            ]
            if line_ids:
                lines = self.env["sale.order.line"].browse(line_ids)
                blocked_orders = lines.mapped("order_id").filtered(
                    lambda o: o.state not in _EDITABLE_SALE_STATES
                )
                if blocked_orders:
                    names = sorted(set(blocked_orders.mapped("display_name")))
                    raise UserError(
                        _(
                            "Cannot create commission agent on order(s) "
                            "'%(orders)s': past draft. Only a sales "
                            "director can."
                        )
                        % {"orders": ", ".join(names)}
                    )
        records = super().create(vals_list)
        records._check_mutation_allowed()
        return records

    def write(self, vals):
        self._check_mutation_allowed()
        return super().write(vals)

    def unlink(self):
        self._check_mutation_allowed()
        return super().unlink()

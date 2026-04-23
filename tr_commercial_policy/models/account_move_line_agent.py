# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, models
from odoo.exceptions import UserError

# States where mutating the commission agent line is still legitimate.
# Past these states the line agent structure is frozen for everyone
# except sales directors and system admins — same pattern as
# sale_order_line_agent (PR #34) but keyed on the invoice state.
# account.move has no ``sent`` equivalent, so the editable window is
# just ``draft``.
_EDITABLE_MOVE_STATES = ("draft",)


class AccountInvoiceLineAgent(models.Model):
    _inherit = "account.invoice.line.agent"

    def _has_post_confirm_bypass(self):
        return self.env.user.has_group(
            "tr_commercial_policy.group_sales_director"
        ) or self.env.user.has_group("base.group_system")

    def _check_mutation_allowed(self):
        """Freeze the *structure* of commission agent lines once the
        invoice is past draft.

        Mirrors ``sale.order.line.agent._check_mutation_allowed`` on
        the sale side. Does NOT validate the semantic correctness of
        commission_id — state-based freeze only. Once the invoice is
        posted / cancelled, only a sales director (or system admin)
        can mutate the agent line structure (create/write/unlink).
        """
        if self._has_post_confirm_bypass():
            return
        for rec in self:
            move = rec.object_id.move_id
            if move.state in _EDITABLE_MOVE_STATES:
                continue
            raise UserError(
                _(
                    "Cannot modify commission agents on invoice "
                    "'%(move)s': invoice is past draft. Only a sales "
                    "director can."
                )
                % {"move": move.display_name}
            )

    @api.model_create_multi
    def create(self, vals_list):
        if not self._has_post_confirm_bypass():
            line_ids = [
                vals.get("object_id") for vals in vals_list if vals.get("object_id")
            ]
            if line_ids:
                lines = self.env["account.move.line"].browse(line_ids)
                blocked_moves = lines.mapped("move_id").filtered(
                    lambda m: m.state not in _EDITABLE_MOVE_STATES
                )
                if blocked_moves:
                    names = sorted(set(blocked_moves.mapped("display_name")))
                    raise UserError(
                        _(
                            "Cannot create commission agent on invoice(s) "
                            "'%(moves)s': past draft. Only a sales "
                            "director can."
                        )
                        % {"moves": ", ".join(names)}
                    )
        records = super().create(vals_list)
        records._check_mutation_allowed()
        return records

    def _check_settled_mutation_allowed(self, action):
        """Freeze any mutation on a line with an effective
        settlement, even for directors. Only ``base.group_system``
        can force it.

        Checks ``settlement_line_ids`` directly (not the ``settled``
        boolean): ``engenere_commission`` overrides ``settled`` to
        be ``True`` whenever ``amount == 0``, which flips during
        invoice creation and is not the invariant we care about.
        Same rule used by
        ``engenere_commission._check_settle_integrity``.
        """
        if not self or self.env.user.has_group("base.group_system"):
            return
        # sudo() needed: settlement_line_ids / settlement.state are
        # guarded by Commissions/Manager ACL. A salesman writing an
        # agent line in draft must still go through this check, so
        # read settlement state with elevated privileges.
        settled = self.sudo().filtered(
            lambda rec: rec.settlement_line_ids.filtered(
                lambda sl: sl.settlement_id.state != "cancel"
            )
        )
        if not settled:
            return
        names = sorted(set(settled.mapped("invoice_id.display_name")))
        raise UserError(
            _(
                "Cannot %(action)s commission agent on invoice(s) "
                "'%(moves)s': line is already settled. Only a "
                "system admin can."
            )
            % {"action": action, "moves": ", ".join(names)}
        )

    def write(self, vals):
        self._check_settled_mutation_allowed("modify")
        self._check_mutation_allowed()
        return super().write(vals)

    def unlink(self):
        self._check_settled_mutation_allowed("unlink")
        self._check_mutation_allowed()
        return super().unlink()

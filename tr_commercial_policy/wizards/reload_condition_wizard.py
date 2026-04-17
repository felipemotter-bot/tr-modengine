# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class ReloadConditionWizard(models.TransientModel):
    _name = "tr.reload.condition.wizard"
    _description = "Reload Commercial Conditions Wizard"

    order_id = fields.Many2one("sale.order", required=True, readonly=True)
    partner_id = fields.Many2one(related="order_id.partner_id", readonly=True)
    condition_id = fields.Many2one(
        related="order_id.commercial_condition_id", readonly=True
    )

    # --- Header change summaries (formatted "X → Y") ---
    pricelist_change = fields.Char(string="Pricelist", readonly=True)
    cash_discount_change = fields.Char(string="Cash Discount", readonly=True)
    fob_discount_change = fields.Char(string="FOB Discount", readonly=True)
    contractual_return_change = fields.Char(string="Contractual Return", readonly=True)

    # --- Line changes ---
    line_ids = fields.One2many(
        "tr.reload.condition.wizard.line",
        "wizard_id",
        string="Lines with Changes",
        readonly=True,
    )
    lines_changed_count = fields.Integer(
        compute="_compute_lines_changed_count",
    )
    has_changes = fields.Boolean(compute="_compute_has_changes")

    @api.depends("line_ids")
    def _compute_lines_changed_count(self):
        for wizard in self:
            wizard.lines_changed_count = len(wizard.line_ids)

    @api.depends(
        "pricelist_change",
        "cash_discount_change",
        "fob_discount_change",
        "contractual_return_change",
        "line_ids",
    )
    def _compute_has_changes(self):
        for wizard in self:
            wizard.has_changes = (
                bool(wizard.pricelist_change)
                or bool(wizard.cash_discount_change)
                or bool(wizard.fob_discount_change)
                or bool(wizard.contractual_return_change)
                or bool(wizard.line_ids)
            )

    def action_apply(self):
        """Apply the reload — delegate to the order's reload method."""
        self.ensure_one()
        self.order_id._apply_reload_conditions()
        return {"type": "ir.actions.act_window_close"}


class ReloadConditionWizardLine(models.TransientModel):
    _name = "tr.reload.condition.wizard.line"
    _description = "Reload Condition Wizard Line"

    wizard_id = fields.Many2one(
        "tr.reload.condition.wizard",
        required=True,
        ondelete="cascade",
    )
    sale_line_id = fields.Many2one("sale.order.line", readonly=True)
    product_id = fields.Many2one("product.product", readonly=True)
    line_description = fields.Char(string="Line", readonly=True)
    change_description = fields.Text(string="Changes", readonly=True)

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = "res.partner"

    is_sales_director = fields.Boolean(
        compute="_compute_is_sales_director",
    )

    @api.depends_context("uid")
    def _compute_is_sales_director(self):
        is_director = self.env.user.has_group(
            "tr_commercial_policy.group_sales_director"
        )
        for partner in self:
            partner.is_sales_director = is_director

    sales_profile_id = fields.Many2one(
        comodel_name="tr.sales.profile",
        string="Sales Profile",
        company_dependent=True,
        domain="[('company_id', '=', current_company_id)]",
        help="Sales profile for this agent/salesperson. "
        "Overrides team and global default profiles.",
    )
    commercial_condition_id = fields.Many2one(
        comodel_name="partner.commercial.condition",
        string="Commercial Condition",
        company_dependent=True,
    )
    effective_condition_id = fields.Many2one(
        comodel_name="partner.commercial.condition",
        string="Effective Commercial Condition",
        compute="_compute_effective_condition_id",
    )
    condition_inherited = fields.Boolean(
        compute="_compute_effective_condition_id",
    )
    condition_inherited_from = fields.Char(
        compute="_compute_effective_condition_id",
    )
    condition_is_override = fields.Boolean(
        compute="_compute_effective_condition_id",
        help="True when this partner has its own condition that overrides "
        "the group condition.",
    )
    is_group_head = fields.Boolean(
        compute="_compute_is_group_head",
    )
    group_member_count = fields.Integer(
        compute="_compute_is_group_head",
    )

    @api.depends("company_group_member_ids")
    def _compute_is_group_head(self):
        for partner in self:
            members = partner.company_group_member_ids
            partner.is_group_head = bool(members)
            partner.group_member_count = len(members)

    def action_view_commercial_condition(self):
        """Open the effective commercial condition."""
        self.ensure_one()
        condition = self.effective_condition_id
        if not condition:
            return self.action_create_commercial_condition()
        return {
            "type": "ir.actions.act_window",
            "res_model": "partner.commercial.condition",
            "res_id": condition.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_create_commercial_condition(self):
        """Create a new commercial condition for this partner."""
        self.ensure_one()
        condition = (
            self.env["partner.commercial.condition"]
            .sudo()
            .create({"partner_id": self.id})
        )
        # ``sudo()`` + explicit ``write()`` (not the attribute-setter
        # shorthand) so that the write actually runs under SUPERUSER
        # and tr_sales_rep_access's guard on Active partners lets
        # this module-owned field update pass.
        self.sudo().write({"commercial_condition_id": condition.id})
        return {
            "type": "ir.actions.act_window",
            "res_model": "partner.commercial.condition",
            "res_id": condition.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_create_override_condition(self):
        """Create an override condition copying values from the group."""
        self.ensure_one()
        group_condition = (
            self.company_group_id.commercial_condition_id
            if self.company_group_id
            else False
        )
        vals = {"partner_id": self.id}
        if group_condition:
            vals.update(
                {
                    "pricelist_id": group_condition.pricelist_id.id,
                    "cash_discount": group_condition.cash_discount,
                    "fob_discount": group_condition.fob_discount,
                    "seller_discount": group_condition.seller_discount,
                    "contractual_return": group_condition.contractual_return,
                }
            )
        condition = self.env["partner.commercial.condition"].sudo().create(vals)
        # ``sudo()`` + explicit ``write()`` (not the attribute-setter
        # shorthand) so that the write actually runs under SUPERUSER
        # and tr_sales_rep_access's guard on Active partners lets
        # this module-owned field update pass.
        self.sudo().write({"commercial_condition_id": condition.id})
        return {
            "type": "ir.actions.act_window",
            "res_model": "partner.commercial.condition",
            "res_id": condition.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_remove_override_condition(self):
        """Remove the override and inherit from group again."""
        self.ensure_one()
        own_condition = self.commercial_condition_id
        # ``sudo()`` + explicit ``write()`` (not the attribute-setter
        # shorthand) so that the write actually runs under SUPERUSER
        # and tr_sales_rep_access's guard on Active partners lets
        # this module-owned field update pass.
        self.sudo().write({"commercial_condition_id": False})
        if own_condition and own_condition.partner_id == self:
            own_condition.sudo().unlink()
        return True

    @api.depends(
        "commercial_condition_id",
        "company_group_id",
        "company_group_id.commercial_condition_id",
    )
    def _compute_effective_condition_id(self):
        for partner in self:
            # Use _origin for company_dependent fields in onchange context
            own = (
                partner._origin.commercial_condition_id
                if hasattr(partner, "_origin") and partner._origin
                else partner.commercial_condition_id
            )
            group = (
                partner.company_group_id
                if hasattr(partner, "company_group_id")
                else False
            )
            group_condition = group.commercial_condition_id if group else False

            is_own_group = group and partner.id == group.id
            if own and group_condition and own == group_condition and not is_own_group:
                # Same condition as group = inherited (not override)
                # But not if partner IS the group head itself
                partner.effective_condition_id = own
                partner.condition_inherited = True
                partner.condition_inherited_from = group.display_name
                partner.condition_is_override = False
            elif own:
                partner.effective_condition_id = own
                partner.condition_inherited = False
                partner.condition_inherited_from = False
                partner.condition_is_override = bool(
                    group_condition and own != group_condition
                )
            elif group_condition:
                partner.effective_condition_id = group_condition
                partner.condition_inherited = True
                partner.condition_inherited_from = group.display_name
                partner.condition_is_override = False
            else:
                partner.effective_condition_id = False
                partner.condition_inherited = False
                partner.condition_inherited_from = False
                partner.condition_is_override = False

    # property_product_pricelist, property_payment_term_id,
    # customer_payment_mode_id, sale_incoterm_id,
    # property_delivery_carrier_id are synced from condition via
    # _sync_partner_fields_from_condition (keeps company_dependent behavior)

    # Mapping: condition field → partner field
    _CONDITION_TO_PARTNER_FIELDS = {
        "pricelist_id": "property_product_pricelist",
        "payment_term_id": "property_payment_term_id",
        "payment_mode_id": "customer_payment_mode_id",
        "incoterm_id": "sale_incoterm_id",
        "delivery_carrier_id": "property_delivery_carrier_id",
        "contractual_return": "punctuality_discount",
    }

    def _sync_partner_fields_from_condition(self):
        """Sync property fields from effective commercial condition.

        Keeps company_dependent behavior intact for multi-company and
        direct DB queries. Only syncs fields that exist on the partner
        model (safe for partial module installations).

        The write is ``with_company(condition.company_id)`` so the
        resulting ``ir.property`` rows land in the company-scope of the
        condition being copied — not in the current user's default
        company. Without this, editing a TRENTO condition while the
        user had TREINAMENTO active would update the partner's
        ``ir.property`` against TREINAMENTO, reintroducing exactly the
        cross-company drift this PR is closing.
        """
        for partner in self:
            condition = partner.effective_condition_id
            if not condition:
                continue
            vals = {}
            for cond_field, partner_field in self._CONDITION_TO_PARTNER_FIELDS.items():
                value = getattr(condition, cond_field, False)
                if hasattr(value, "id"):
                    vals[partner_field] = value.id if value else False
                else:
                    vals[partner_field] = value
            if vals:
                partner.sudo().with_company(condition.company_id).write(vals)

    def write(self, vals):
        result = super().write(vals)
        if "commercial_condition_id" in vals:
            self._sync_partner_fields_from_condition()
        return result

    @api.onchange("company_group_id")
    def _onchange_company_group_id(self):
        """Warn about condition conflict instead of pricelist."""
        res = {}
        if not self.company_group_id:
            return res
        group_condition = self.company_group_id.commercial_condition_id
        # Use _origin for company_dependent fields in onchange context
        own_condition = (
            self._origin.commercial_condition_id
            if self._origin
            else self.commercial_condition_id
        )
        if own_condition and group_condition and own_condition != group_condition:
            res["warning"] = {
                "title": _("Commercial Condition Override"),
                "message": _(
                    "The group '%(group)s' has a commercial condition, "
                    "but this customer's own condition will override it. "
                    "To inherit the group condition instead, go to the "
                    "Sales & Purchase tab and click 'Remove Override'.",
                    group=self.company_group_id.display_name,
                ),
            }
        elif own_condition and not group_condition:
            pass  # Group has no condition, no conflict
        elif group_condition and not own_condition:
            res["warning"] = {
                "title": _("Warning"),
                "message": _(
                    "The group '%(group)s' has a commercial condition "
                    "that will be inherited by this customer.",
                    group=self.company_group_id.display_name,
                ),
            }
        return res

    @api.constrains("commercial_condition_id")
    def _check_condition_belongs_to_partner_or_group(self):
        """Condition must belong to the partner itself or its company group."""
        for partner in self:
            condition = partner.commercial_condition_id
            if not condition:
                continue
            allowed = partner
            if hasattr(partner, "company_group_id") and partner.company_group_id:
                allowed |= partner.company_group_id
            if condition.partner_id not in allowed:
                raise ValidationError(
                    _(
                        "The commercial condition belongs to '%(owner)s' "
                        "and cannot be assigned to '%(partner)s'. "
                        "A condition can only be shared within the same "
                        "company group.",
                        owner=condition.partner_id.display_name,
                        partner=partner.display_name,
                    )
                )

    @api.constrains("agent_ids")
    def _check_single_agent(self):
        for partner in self:
            if len(partner.agent_ids) > 1:
                raise ValidationError(
                    _(
                        "The customer '%(partner)s' can have at most one "
                        "sales agent. Please remove the extra agents."
                    )
                    % {"partner": partner.display_name}
                )

    @api.constrains("agent_ids")
    def _check_agent_has_profile(self):
        for partner in self:
            for agent in partner.agent_ids:
                if not agent.sales_profile_id:
                    raise ValidationError(
                        _(
                            "The agent '%(agent)s' assigned to customer "
                            "'%(partner)s' does not have a sales profile. "
                            "Please assign a profile to the agent before "
                            "linking it to a customer."
                        )
                        % {
                            "agent": agent.display_name,
                            "partner": partner.display_name,
                        }
                    )

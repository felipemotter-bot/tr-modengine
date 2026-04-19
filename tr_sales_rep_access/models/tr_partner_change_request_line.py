# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

EDITABLE_PARTNER_FIELDS = (
    "phone",
    "mobile",
    "email",
    "street",
    "street2",
    "city",
    "zip",
    "state_id",
    "country_id",
)


class TrPartnerChangeRequestLine(models.Model):
    _name = "tr.partner.change.request.line"
    _description = "Partner Change Request Line"

    request_id = fields.Many2one(
        "tr.partner.change.request",
        required=True,
        ondelete="cascade",
        index=True,
    )
    field_id = fields.Many2one(
        "ir.model.fields",
        string="Field",
        required=True,
        ondelete="cascade",
        domain=lambda self: [
            ("model", "=", "res.partner"),
            ("name", "in", list(EDITABLE_PARTNER_FIELDS)),
        ],
    )
    field_ttype = fields.Selection(
        related="field_id.ttype",
        store=False,
        compute_sudo=True,
    )
    new_value_char = fields.Char(string="New value (text)")
    new_value_reference = fields.Reference(
        selection=[
            ("res.country", "Country"),
            ("res.country.state", "State"),
        ],
        string="New value (reference)",
    )

    @api.model_create_multi
    def create(self, vals_list):
        request_model = self.env["tr.partner.change.request"]
        for vals in vals_list:
            # ``request_id`` is required=True so it is always in
            # ``vals`` by the time we get here.
            request = request_model.browse(vals["request_id"]).exists()
            if request and request.state != "pending":
                raise AccessError(
                    _(
                        "Cannot add a line to a change request that "
                        "is not in the pending state."
                    )
                )
        return super().create(vals_list)

    def write(self, vals):
        for line in self:
            if line.request_id.state != "pending":
                raise AccessError(
                    _(
                        "Cannot edit a line of a change request that "
                        "is not in the pending state."
                    )
                )
        return super().write(vals)

    def unlink(self):
        for line in self:
            if line.request_id.state != "pending":
                raise AccessError(
                    _(
                        "Cannot remove a line of a change request "
                        "that is not in the pending state."
                    )
                )
        return super().unlink()

    @api.constrains("field_id")
    def _check_field_in_whitelist(self):
        # ``sudo`` on ``field_id`` because ``ir.model.fields`` is not
        # readable by the rep group; without it, the constraint
        # raises CacheMiss instead of the user-friendly
        # ValidationError.
        for line in self:
            field = line.field_id.sudo()
            if field.name not in EDITABLE_PARTNER_FIELDS:
                raise ValidationError(
                    _(
                        "Field %(field)s is not allowed in a partner "
                        "change request. The whitelist is: "
                        "%(whitelist)s."
                    )
                    % {
                        "field": field.name or "(empty)",
                        "whitelist": ", ".join(EDITABLE_PARTNER_FIELDS),
                    }
                )

    @api.constrains("field_id", "new_value_char", "new_value_reference")
    def _check_payload_matches_field(self):
        for line in self:
            # ``field_id`` is required=True so it is always set
            # when this constraint fires.
            field = line.field_id.sudo()
            ttype = field.ttype
            if ttype == "char":
                if line.new_value_reference:
                    raise ValidationError(
                        _(
                            "Field %s expects a text value; the "
                            "reference field must stay empty."
                        )
                        % field.name
                    )
            elif ttype == "many2one":
                if not line.new_value_reference:
                    raise ValidationError(
                        _(
                            "Field %s expects a reference value; "
                            "use the reference field."
                        )
                        % field.name
                    )
                if line.new_value_char:
                    raise ValidationError(
                        _(
                            "Field %s expects a reference value; "
                            "the text field must stay empty."
                        )
                        % field.name
                    )
                expected_model = field.relation
                ref_model = line.new_value_reference._name
                if ref_model != expected_model:
                    raise ValidationError(
                        _(
                            "Field %(field)s expects a reference to "
                            "%(expected)s, got %(actual)s instead."
                        )
                        % {
                            "field": field.name,
                            "expected": expected_model,
                            "actual": ref_model,
                        }
                    )

    def _resolve_value(self):
        """Return the Python value to write on the partner for this line."""
        self.ensure_one()
        if self.field_id.sudo().ttype == "char":
            return self.new_value_char or False
        return self.new_value_reference.id if self.new_value_reference else False

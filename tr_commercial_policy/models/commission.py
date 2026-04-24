# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from psycopg2 import IntegrityError

from odoo.tools import float_round
from odoo.tools.safe_eval import safe_eval, test_python_expr

_logger = logging.getLogger(__name__)

COMMISSION_TEMPLATE_PARAM = "tr.commission_formula_template"

# Labels for invoice_state used in commission naming
INVOICE_STATE_LABELS = {
    "partner_confirm": "NA CONFIRMAÇÃO",
    "partial_paid": "NO PAGAMENTO",
    "open": "NA FATURA",
    "paid": "NO PAGAMENTO TOTAL",
}


class Commission(models.Model):
    _inherit = "commission"

    tr_managed = fields.Boolean(
        default=False,
        help="Created/managed by the commercial policy.",
    )
    tr_rate = fields.Float(
        digits="Discount Policy",
        help="The commission rate (%) this record represents.",
    )

    def _auto_init(self):
        result = super()._auto_init()
        self.env.cr.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                commission_tr_managed_unique
            ON commission (invoice_state, tr_rate)
            WHERE tr_managed = TRUE
        """
        )
        return result

    @api.constrains("formula", "tr_rate", "tr_managed")
    def _check_tr_formula_consistency(self):
        """Ensure managed commissions match the template exactly."""
        for record in self:
            if not record.tr_managed:
                continue
            if self.env.context.get("tr_template_propagation"):
                continue
            template = (
                self.env["ir.config_parameter"]
                .sudo()
                .get_param(COMMISSION_TEMPLATE_PARAM, default="")
            )
            if not template:
                continue
            expected = template.replace("{rate}", str(record.tr_rate / 100))
            if record.formula and record.formula.strip() != expected.strip():
                raise ValidationError(
                    _(
                        "Commission '%(name)s' is managed by the commercial"
                        " policy. Its formula must match the template."
                        " Please do not edit it manually."
                    )
                    % {"name": record.name}
                )

    @api.model
    def _normalize_rate(self, rate):
        """Round rate to the precision of 'Discount Policy'."""
        precision = self.env["decimal.precision"].precision_get("Discount Policy")
        return float_round(rate, precision_digits=precision)

    @api.model
    def _validate_formula_template(self, template, rate=5.0):
        """Validate a formula template before using it.

        Checks:
        1. Structural: {rate} and 'result' present, non-empty
        2. Syntactic: compiles after replacing {rate} with a test value
        3. Semantic: produces correct numeric result in 3 business scenarios
        """
        if not template or not template.strip():
            raise UserError(_("Commission formula template is empty."))
        if "{rate}" not in template:
            raise UserError(
                _("Commission formula template must contain the" " placeholder {rate}.")
            )
        if "result" not in template:
            raise UserError(
                _("Commission formula template must assign a value" " to 'result'.")
            )
        # Syntactic: test that it compiles
        test_formula = template.replace("{rate}", str(rate / 100))
        err = test_python_expr(expr=test_formula, mode="exec")
        if err:
            raise UserError(
                _("Commission formula template has a syntax error:\n%s") % err
            )
        # Semantic: 3 business scenarios
        # (model_name, fiscal_amount_untaxed, punctuality%, expected_result)
        scenarios = [
            ("sale.order.line", 1000.0, 0.0, 1000.0 * (rate / 100)),
            ("sale.order.line", 1000.0, 10.0, 1000.0 * (rate / 100) * 0.9),
            (
                "account.move.line",
                1000.0,
                5.0,
                1000.0 * (rate / 100) * 0.95,
            ),
        ]
        for model_name, untaxed, punctuality, expected in scenarios:
            fake_line = type(
                "FakeLine",
                (),
                {
                    "_name": model_name,
                    "fiscal_amount_untaxed": untaxed,
                    "price_subtotal": untaxed,
                    "order_id": type(
                        "FakeOrder",
                        (),
                        {
                            "punctuality_discount": punctuality,
                        },
                    )(),
                    "move_id": type(
                        "FakeMove",
                        (),
                        {
                            "invoice_punctuality_discount": punctuality,
                        },
                    )(),
                },
            )()
            eval_ctx = {"line": fake_line, "self": None}
            try:
                safe_eval(test_formula, eval_ctx, mode="exec", nocopy=True)
            except Exception as exc:
                raise UserError(
                    _(
                        "Commission formula template failed for"
                        " scenario %(model)s:\n%(err)s"
                    )
                    % {"model": model_name, "err": exc}
                ) from exc
            result_val = eval_ctx.get("result")
            if not isinstance(result_val, (int, float)):
                raise UserError(
                    _(
                        "Commission formula template did not produce a"
                        " numeric result (got %(type)s) for scenario"
                        " %(model)s."
                    )
                    % {
                        "type": type(result_val).__name__,
                        "model": model_name,
                    }
                )
            if result_val < 0:
                raise UserError(
                    _(
                        "Commission formula template produced a negative"
                        " result (%(val).4f) for scenario %(model)s."
                    )
                    % {"val": result_val, "model": model_name}
                )
            if abs(result_val - expected) > 0.0001:
                raise UserError(
                    _(
                        "Commission formula template produced %(got).4f"
                        " but expected %(exp).4f for scenario %(model)s"
                        " (base=%(base).0f, rate=%(rate).2f%%,"
                        " punctuality=%(punct).0f%%)."
                    )
                    % {
                        "got": result_val,
                        "exp": expected,
                        "model": model_name,
                        "base": untaxed,
                        "rate": rate,
                        "punct": punctuality,
                    }
                )

    @api.model
    def _find_managed_commission(self, invoice_state, rate):
        """Search-only: find an existing managed commission (no create).

        Normalizes rate internally for safe comparison.
        """
        rate = self._normalize_rate(rate)
        return self.search(
            [
                ("tr_managed", "=", True),
                ("invoice_state", "=", invoice_state),
                ("tr_rate", "=", rate),
            ],
            limit=1,
        )

    @api.model
    def _ensure_managed_commission(self, invoice_state, rate):
        """Ensure a managed commission exists for (invoice_state, rate).

        Deterministic: same (invoice_state, rate) always returns the same record.
        Rate is normalized before search/create to align with the DB index.
        On concurrent create, catches IntegrityError and retries search.
        """
        rate = self._normalize_rate(rate)
        # Search existing managed commission
        existing = self._find_managed_commission(invoice_state, rate)
        if existing:
            return existing
        # Not found — create from template
        template = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param(COMMISSION_TEMPLATE_PARAM, default="")
        )
        self._validate_formula_template(template, rate=rate)
        formula = template.replace("{rate}", str(rate / 100))
        label = INVOICE_STATE_LABELS.get(invoice_state, invoice_state or "")
        name = "%.2f%% %s" % (rate, label)
        for _attempt in range(3):  # pragma: no cover — race condition retry
            try:
                with self.env.cr.savepoint():
                    # Managed commissions are deterministic infra records
                    # generated from an admin-configured template; regular
                    # salespeople only need to read them, not to hold
                    # create rights on the commission model.
                    return self.sudo().create(
                        {
                            "name": name,
                            "commission_type": "formula",
                            "amount_base_type": "gross_amount",
                            "invoice_state": invoice_state,
                            "formula": formula,
                            "tr_managed": True,
                            "tr_rate": rate,
                        }
                    )
            except IntegrityError:
                # Concurrent create — another transaction won the race
                existing = self._find_managed_commission(invoice_state, rate)
                if existing:
                    return existing
        raise UserError(  # pragma: no cover
            _(  # pragma: no cover
                "Could not create managed commission for rate"  # pragma: no cover
                " %(rate).2f%%. Please retry."  # pragma: no cover
            )  # pragma: no cover
            % {"rate": rate}  # pragma: no cover
        )  # pragma: no cover

    @api.model
    def _propagate_template(self):
        """Update all managed commissions with the current template.

        Called when the system parameter changes.
        """
        template = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param(COMMISSION_TEMPLATE_PARAM, default="")
        )
        managed = self.search([("tr_managed", "=", True)])
        if not managed:
            # Validate once with default rate even if no records exist
            self._validate_formula_template(template)
            return
        # Validate with each managed record's rate
        for record in managed:
            self._validate_formula_template(template, rate=record.tr_rate)
        for record in managed:
            new_formula = template.replace("{rate}", str(record.tr_rate / 100))
            record.with_context(tr_template_propagation=True).formula = new_formula
        _logger.info(
            "Propagated commission template to %d managed records.",
            len(managed),
        )


class IrConfigParameter(models.Model):
    _inherit = "ir.config_parameter"

    def write(self, vals):
        """Propagate template changes to managed commissions."""
        result = super().write(vals)
        if "value" in vals and not self.env.context.get("tr_skip_propagation"):
            for record in self:
                if record.key == COMMISSION_TEMPLATE_PARAM:
                    self.env["commission"]._propagate_template()
                    break
        return result

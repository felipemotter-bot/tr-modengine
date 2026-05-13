# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError
from odoo.tools import float_compare


def calc_adjustment_factor(contractual_return_pct, tax_rate, freight_rate, admin_rate):
    """Calculate adjustment factor from contractual return and rates.

    Returns the percentage by which the reference price differs from
    the base price (e.g. +3.5 means reference is 3.5% above base).

    ``tax_rate``, ``freight_rate`` and ``admin_rate`` compose additively
    as deductions from the gross price. The factor inflates the
    reference so the net (after all deductions including the
    contractual return) matches the net you would get without the
    contractual return.
    """
    cr_decimal = (contractual_return_pct or 0.0) / 100.0
    if cr_decimal <= 0:
        return 0.0
    total_rate = tax_rate + freight_rate + admin_rate
    numerator = 1 - total_rate
    denominator = 1 - cr_decimal - total_rate
    if denominator <= 0:
        return 0.0
    return (numerator / denominator - 1) * 100


def get_seller_markup_max_pct(env):
    """Return the global seller markup maximum as a percentage (float).

    Reads ``tr_commercial_policy.seller_markup_max_pct`` from
    ``ir.config_parameter``.  A value of 0.0 means no markup is allowed.
    """
    icp = env["ir.config_parameter"].sudo()
    return float(icp.get_param("tr_commercial_policy.seller_markup_max_pct", "0.0"))


def validate_seller_markup(env, seller_discount):
    """Raise ValidationError when seller_discount violates the markup limit.

    Negative seller_discount means markup.  The allowed floor is
    ``-get_seller_markup_max_pct(env)``.  A zero markup_max means no
    negative discount is accepted.
    """
    from odoo import _
    from odoo.exceptions import ValidationError

    markup_max = get_seller_markup_max_pct(env)
    if (seller_discount or 0.0) < -markup_max:
        raise ValidationError(
            _(
                "Seller markup (%(markup).2f%%) exceeds the maximum"
                " allowed markup (%(max).2f%%)."
            )
            % {
                "markup": -(seller_discount or 0.0),
                "max": markup_max,
            }
        )


def get_policy_rates(env):
    """Return (tax_rate, freight_rate, admin_rate) as decimals.

    Read from ``ir.config_parameter`` under the ``*_pct`` keys (values
    stored as percentage integers) and divide by 100 to return decimal
    rates ready for use in calculations.

    Source of truth: the three settings exposed under
    ``Configuração → Vendas → Commercial Policy``. Configure via
    ``res.config.settings`` — manual edits of the underlying
    ``ir.config_parameter`` keys are discouraged.
    """
    icp = env["ir.config_parameter"].sudo()
    return (
        float(icp.get_param("tr_commercial_policy.tax_rate_pct", "0.0")) / 100.0,
        float(icp.get_param("tr_commercial_policy.freight_rate_pct", "0.0")) / 100.0,
        float(icp.get_param("tr_commercial_policy.admin_rate_pct", "0.0")) / 100.0,
    )


def calc_reference_price(
    base_price, contractual_return_pct, tax_rate, freight_rate, admin_rate
):
    """Calculate reference price from base price and contractual return."""
    if not base_price:
        return 0.0
    factor = calc_adjustment_factor(
        contractual_return_pct, tax_rate, freight_rate, admin_rate
    )
    return base_price * (1 + factor / 100)


def calc_price_unit(reference_price, seller_discount, extra_discount):
    """Calculate unit price from reference price and discounts."""
    if not reference_price:
        return 0.0
    total_disc = (seller_discount or 0) + (extra_discount or 0)
    return reference_price * (1 - total_disc / 100)


def check_cash_discount_limit(cash_discount, profile_max):
    """Return True if cash discount exceeds profile maximum."""
    return (cash_discount or 0) > (profile_max or 0)


def check_fob_discount_limit(fob_discount, profile_max):
    """Return True if FOB discount exceeds profile maximum."""
    return (fob_discount or 0) > (profile_max or 0)


def check_payment_term_limit(cash_discount, avg_days, max_days):
    """Return True if payment term violates the cash-discount rule.

    Only applies when cash_discount > 0 and max_days is set.
    """
    if not cash_discount or not max_days:
        return False
    return (avg_days or 0) > max_days


def get_extra_discount_approval_level(extra_discount, manager_limit):
    """Return approval level for a given extra discount.

    Returns "director" if above manager limit, "manager" if any
    extra discount is present, or "none" if zero.
    """
    if not extra_discount or extra_discount <= 0:
        return "none"
    if extra_discount > (manager_limit or 0):
        return "director"
    return "manager"


def calc_line_pricing(
    base_price,
    contractual_return,
    seller_discount,
    extra_discount,
    cash_discount,
    fob_discount,
    qty,
    tax_rate,
    freight_rate,
    admin_rate,
):
    """Full pricing calculation for a sale order line.

    Composes the individual calculation functions into a single call.
    Pure function — no ORM, no side effects.

    Returns dict with: adjustment_factor, reference_price,
    price_unit, discount, discount_value.
    """
    adjustment_factor = calc_adjustment_factor(
        contractual_return, tax_rate, freight_rate, admin_rate
    )
    reference_price = calc_reference_price(
        base_price, contractual_return, tax_rate, freight_rate, admin_rate
    )
    price_unit = calc_price_unit(reference_price, seller_discount, extra_discount)
    discount = (cash_discount or 0) + (fob_discount or 0)
    discount_value = (qty or 0) * price_unit * discount / 100 if discount else 0.0
    return {
        "adjustment_factor": adjustment_factor,
        "reference_price": reference_price,
        "price_unit": price_unit,
        "discount": discount,
        "discount_value": discount_value,
    }


def _rule_passes_qty_min(rule, line_qty, line_uom):
    """Return True when ``rule.qty_min`` is met by the line.

    - ``qty_min == 0`` ⇒ rule always passes (no qty filter).
    - ``qty_min > 0`` ⇒ converts ``line_qty`` from ``line_uom`` to
      ``rule.qty_uom_id`` and compares with ``float_compare`` using the
      rule UoM rounding.
    - Same UoM ⇒ no conversion needed.
    - Different UoM categories ⇒ rule ignored (returns False).
    - ``UserError`` from ``_compute_quantity`` (e.g. inactive UoM) ⇒
      defensive fallback, rule ignored.
    """
    if rule.qty_min <= 0:
        return True
    rule_uom = rule.qty_uom_id
    if not rule_uom or not line_uom:
        return False
    if line_uom == rule_uom:
        converted = line_qty
    else:
        if line_uom.category_id != rule_uom.category_id:
            return False
        try:
            converted = line_uom._compute_quantity(line_qty, rule_uom, round=False)
        except UserError:
            return False
    return (
        float_compare(converted, rule.qty_min, precision_rounding=rule_uom.rounding)
        >= 0
    )


def _pick_best(rules):
    """Pick the rule that wins among elegible rules in the same level.

    Tiebreaker order: largest ``qty_min`` first, then smallest
    ``sequence``, then smallest ``id``. Largest ``qty_min`` wins so that
    cadastrating multiple rules in the same scope with increasing
    thresholds produces volume bands automatically.
    """
    return rules.sorted(key=lambda r: (-r.qty_min, r.sequence, r.id))[0]


def resolve_applicable_rule(product, rules, empty_rule, line_qty, line_uom):
    """Resolve the most specific profile rule for a product and line qty.

    Resolution order: product variant > product template > category > general.
    Within each level, only rules whose ``qty_min`` is met by ``line_qty``
    (in ``line_uom``) are eligible; the rule with the largest matching
    ``qty_min`` wins (``sequence`` as tiebreaker). When no rule at a level
    is eligible, resolution falls back to the next level.

    Returns the matching rule record or ``empty_rule`` (empty recordset).

    Pure ORM function — shared between sale.order.line and account.move.line.

    ``line_qty`` and ``line_uom`` are required (no defaults). This is
    intentional: any caller that forgets to pass them would silently bypass
    every volume rule, which is a much worse failure mode than a TypeError.
    """
    # 1. Product variant
    variant_rules = rules.filtered(
        lambda rule: rule.applied_on == "product" and rule.product_id == product
    )
    eligible = variant_rules.filtered(
        lambda r: _rule_passes_qty_min(r, line_qty, line_uom)
    )
    if eligible:
        return _pick_best(eligible)
    # 2. Product template
    tmpl_rules = rules.filtered(
        lambda rule: rule.applied_on == "product_template"
        and rule.product_tmpl_id == product.product_tmpl_id
    )
    eligible = tmpl_rules.filtered(
        lambda r: _rule_passes_qty_min(r, line_qty, line_uom)
    )
    if eligible:
        return _pick_best(eligible)
    # 3. Category (walk up the category tree)
    categ = product.categ_id
    while categ:
        categ_rules = rules.filtered(
            lambda rule, cat=categ: rule.applied_on == "category"
            and rule.categ_id == cat
        )
        eligible = categ_rules.filtered(
            lambda r: _rule_passes_qty_min(r, line_qty, line_uom)
        )
        if eligible:
            return _pick_best(eligible)
        categ = categ.parent_id
    # 4. General
    general_rules = rules.filtered(lambda rule: rule.applied_on == "general")
    eligible = general_rules.filtered(
        lambda r: _rule_passes_qty_min(r, line_qty, line_uom)
    )
    if eligible:
        return _pick_best(eligible)
    return empty_rule


def locked_covers_line(locked, regular):
    """True when a locked condition.line covers the scope of a regular
    condition.line in the same condition.

    Pairs (locked.applied_on ↔ regular.applied_on):
      - product       ↔ product       : same product_id
      - product_tmpl  ↔ product_tmpl  : same product_tmpl_id
      - product_tmpl  ↔ product       : regular.product_id.product_tmpl_id
                                        == locked.product_tmpl_id
      - product       ↔ product_tmpl  : never (locked variant is narrower
                                        than regular template — line
                                        template still covers OTHER
                                        variants of the same template).

    Both records must be active; locked must be is_locked; regular must
    not be is_locked. Caller is responsible for the role pairing.
    """
    if not locked or not regular:
        return False
    if not locked.active or not regular.active:
        return False
    if not locked.is_locked or regular.is_locked:
        return False
    k_scope = locked.applied_on
    l_scope = regular.applied_on
    if k_scope == "product":
        return l_scope == "product" and locked.product_id == regular.product_id
    if k_scope == "product_template":
        if l_scope == "product_template":
            return locked.product_tmpl_id == regular.product_tmpl_id
        if l_scope == "product":
            return regular.product_id.product_tmpl_id == locked.product_tmpl_id
    return False

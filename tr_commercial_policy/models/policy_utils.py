# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).


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


def resolve_applicable_rule(product, rules, empty_rule):
    """Resolve the most specific profile rule for a product.

    Resolution order: product variant > product template > category > general.
    Returns the matching rule record or ``empty_rule`` (empty recordset).

    Pure ORM function — shared between sale.order.line and account.move.line.
    """
    # 1. Product variant
    variant_rule = rules.filtered(
        lambda rule: rule.applied_on == "product" and rule.product_id == product
    )
    if variant_rule:
        return variant_rule[0]
    # 2. Product template
    tmpl_rule = rules.filtered(
        lambda rule: rule.applied_on == "product_template"
        and rule.product_tmpl_id == product.product_tmpl_id
    )
    if tmpl_rule:
        return tmpl_rule[0]
    # 3. Category (walk up the category tree)
    categ = product.categ_id
    while categ:
        categ_rule = rules.filtered(
            lambda rule, cat=categ: rule.applied_on == "category"
            and rule.categ_id == cat
        )
        if categ_rule:
            return categ_rule[0]
        categ = categ.parent_id
    # 4. General
    general_rule = rules.filtered(lambda rule: rule.applied_on == "general")
    if general_rule:
        return general_rule[0]
    return empty_rule

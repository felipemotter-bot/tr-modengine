# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def pre_init_hook(cr):
    """Pre-create stored computed columns and clean dirty data.

    When the ORM installs a module that adds stored computed fields to an
    existing table it marks ALL rows for recompute.  By creating the columns
    beforehand the ORM finds them already present and skips the mass
    recompute — turning a 1 h+ install into < 5 min on large databases.
    """
    _pre_create_columns(cr)
    _clean_orphan_agents(cr)
    _clean_orphan_tier_reviews(cr)


# ---------------------------------------------------------------------------
# pre_init helpers
# ---------------------------------------------------------------------------

# Odoo Float with digits="…" → NUMERIC; Float without digits →
# DOUBLE PRECISION. Using the wrong storage type makes the ORM silently
# drop and recreate the column on next load, wiping any data in it.
_COLUMNS_TO_CREATE = [
    # (table, column, pg_type)
    # sale.order — stored computed fields
    ("sale_order", "sales_profile_id", "INTEGER"),
    ("sale_order", "commercial_condition_id", "INTEGER"),
    ("sale_order", "cash_discount", "NUMERIC"),  # digits=Discount Policy
    ("sale_order", "fob_discount", "NUMERIC"),  # digits=Discount Policy
    ("sale_order", "contractual_return", "DOUBLE PRECISION"),  # no digits
    ("sale_order", "punctuality_discount", "DOUBLE PRECISION"),  # no digits
    ("sale_order", "discount_approval_level", "VARCHAR"),
    ("sale_order", "discount_approval_snapshot", "TEXT"),
    # sale.order.line — stored computed fields
    ("sale_order_line", "base_price", "NUMERIC"),  # digits=Sale Price
    ("sale_order_line", "reference_price", "NUMERIC"),  # digits=Sale Price
    ("sale_order_line", "adjustment_factor", "DOUBLE PRECISION"),  # no digits
    # account.move — stored computed fields
    ("account_move", "discount_approval_level", "VARCHAR"),
    ("account_move", "discount_approval_snapshot", "TEXT"),
]


def _pre_create_columns(cr):
    """Create columns for stored computed fields before the ORM sees them."""
    for table, column, col_type in _COLUMNS_TO_CREATE:
        cr.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
            """,
            (table, column),
        )
        if not cr.fetchone():
            # pylint: disable=sql-injection  -- safe: values from hardcoded list
            cr.execute(
                'ALTER TABLE "{}" ADD COLUMN "{}" {}'.format(table, column, col_type)
            )
            _logger.info("Pre-created column %s.%s (%s).", table, column, col_type)


def _clean_orphan_agents(cr):
    """Remove agent records referencing non-existent order/invoice lines."""
    cr.execute(
        """
        DELETE FROM sale_order_line_agent
        WHERE NOT EXISTS (
            SELECT 1 FROM sale_order_line
            WHERE sale_order_line.id = sale_order_line_agent.object_id
        )
        """
    )
    if cr.rowcount:
        _logger.info("Cleaned %d orphan sale.order.line.agent records.", cr.rowcount)

    cr.execute(
        """
        DELETE FROM account_invoice_line_agent
        WHERE NOT EXISTS (
            SELECT 1 FROM account_move_line
            WHERE account_move_line.id = account_invoice_line_agent.object_id
        )
        """
    )
    if cr.rowcount:
        _logger.info(
            "Cleaned %d orphan account.invoice.line.agent records.", cr.rowcount
        )


def _clean_orphan_tier_reviews(cr):
    """Remove tier reviews pointing at records that no longer exist."""
    for model, table in [
        ("sale.order", "sale_order"),
        ("account.move", "account_move"),
    ]:
        cr.execute(
            """
            DELETE FROM tier_review
            WHERE model = %s
              AND NOT EXISTS (
                  SELECT 1 FROM "{}" WHERE "{}".id = tier_review.res_id
              )
            """.format(table, table),
            (model,),
        )
        if cr.rowcount:
            _logger.info("Cleaned %d orphan tier reviews for %s.", cr.rowcount, model)


# ---------------------------------------------------------------------------
# post_init_hook
# ---------------------------------------------------------------------------


def post_init_hook(cr, registry):
    """Create commercial conditions for partners with confirmed sales."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    _ensure_default_profiles(env)
    _create_conditions_from_partners(env)


# High sequence so any rule the user adds later (default 10) wins
# resolution against this placeholder.
_PLACEHOLDER_SEQUENCE = 999

# discount_up_to=100 so the placeholder accepts any realistic discount;
# admins should override with real bands. commission_rate=0 keeps the
# OCA default (not commission-managed) until the admin configures one.
_AGENT_PLACEHOLDER_RULE = [
    (
        0,
        0,
        {
            "applied_on": "general",
            "sequence": _PLACEHOLDER_SEQUENCE,
            "commission_band_ids": [
                (0, 0, {"discount_up_to": 100.0, "commission_rate": 0.0}),
            ],
        },
    ),
]

_INTERNAL_PLACEHOLDER_RULE = [
    (
        0,
        0,
        {
            "applied_on": "general",
            "sequence": _PLACEHOLDER_SEQUENCE,
            "order_value_band_ids": [
                (0, 0, {"order_min_amount": 0.0, "seller_discount_max": 100.0}),
            ],
        },
    ),
]


def ensure_profiles_have_rules(env):
    """Sanitize existing profiles: any profile without rules gets a
    placeholder rule so the new constraint passes during update.
    """
    Profile = env["tr.sales.profile"]
    profiles_without_rules = Profile.search([("rule_ids", "=", False)])
    for profile in profiles_without_rules:
        if profile.profile_type == "agent":
            profile.write({"rule_ids": _AGENT_PLACEHOLDER_RULE})
        else:
            profile.write({"rule_ids": _INTERNAL_PLACEHOLDER_RULE})
        _logger.info(
            "Added placeholder rule to profile '%s' (id=%d).",
            profile.name,
            profile.id,
        )


def _ensure_default_profiles(env):
    """Create default sales profiles and assign to agents without one."""
    Profile = env["tr.sales.profile"]

    agent_profile = Profile.search([("profile_type", "=", "agent")], limit=1)
    if not agent_profile:
        agent_profile = Profile.create(
            {
                "name": "Default Agent Profile",
                "profile_type": "agent",
                "cash_discount_max": 3.0,
                "fob_discount_max": 3.0,
                "rule_ids": _AGENT_PLACEHOLDER_RULE,
            }
        )
        _logger.info("Created default agent profile (id=%d).", agent_profile.id)

    internal_profile = Profile.search([("profile_type", "=", "internal")], limit=1)
    if not internal_profile:
        Profile.create(
            {
                "name": "Default Internal Profile",
                "profile_type": "internal",
                "cash_discount_max": 3.0,
                "fob_discount_max": 3.0,
                "rule_ids": _INTERNAL_PLACEHOLDER_RULE,
            }
        )
        _logger.info("Created default internal profile.")

    # Assign agent profile to agents without one
    agents_without = env["res.partner"].search(
        [("agent", "=", True), ("sales_profile_id", "=", False)]
    )
    if agents_without:
        for agent in agents_without:
            agent.sales_profile_id = agent_profile
        _logger.info(
            "Assigned default agent profile to %d agents.",
            len(agents_without),
        )


def _create_conditions_from_partners(env):
    """Create conditions using SQL to avoid ORM recompute cascades."""
    cr = env.cr
    # Target company for this migration run: everything the hook creates
    # is anchored to the current admin's default company. Every ``ir.property``
    # read down the chain filters by this company (OR company-less) so a
    # partner preconfigured in multiple companies doesn't leak values from
    # the wrong one into the condition.
    target_company_id = env.company.id

    # Get field_id for ir.property
    cr.execute(
        """
        SELECT id FROM ir_model_fields
        WHERE model = 'res.partner'
          AND name = 'commercial_condition_id'
        """
    )
    row = cr.fetchone()
    if not row:
        _logger.warning("commercial_condition_id field not found, skipping.")
        return
    field_id = row[0]

    # Default pricelist (scoped to the target company or company-less)
    cr.execute(
        """
        SELECT id FROM product_pricelist
        WHERE company_id IS NULL OR company_id = %s
        ORDER BY id LIMIT 1
        """,
        (target_company_id,),
    )
    default_pl = cr.fetchone()
    default_pricelist_id = default_pl[0] if default_pl else 1

    # Partners with sales and no condition
    cr.execute(
        """
        SELECT DISTINCT rp.id, rp.company_group_id
        FROM res_partner rp
        INNER JOIN sale_order so
            ON so.partner_id = rp.id AND so.state IN ('sale', 'done')
        LEFT JOIN ir_property ip
            ON ip.res_id = CONCAT('res.partner,', rp.id::text)
            AND ip.name = 'commercial_condition_id'
            AND ip.value_reference IS NOT NULL
        WHERE ip.id IS NULL
          AND rp.is_company = True
        ORDER BY rp.id
        """
    )
    partners = cr.fetchall()

    if not partners:
        _logger.info("No partners to migrate.")
        return

    _logger.info("Creating conditions for %d partners...", len(partners))

    # Collect group heads and members
    group_heads = {}  # group_id -> [member_ids]
    individuals = []
    for partner_id, group_id in partners:
        if group_id:
            if group_id not in group_heads:
                group_heads[group_id] = []
            if partner_id != group_id:
                group_heads[group_id].append(partner_id)
        else:
            individuals.append(partner_id)

    group_condition_map = _create_group_conditions(
        cr, group_heads, default_pricelist_id, field_id, target_company_id
    )
    member_count = _assign_group_members(
        cr, group_heads, group_condition_map, field_id, target_company_id
    )
    _create_individual_conditions(
        cr, individuals, default_pricelist_id, field_id, target_company_id
    )
    _logger.info(
        "Migration complete: %d groups, %d members, %d individual.",
        len(group_condition_map),
        member_count,
        len(individuals),
    )


def _create_group_conditions(
    cr, group_heads, default_pricelist_id, field_id, target_company_id
):
    """Step 1: Create conditions for group heads."""
    _logger.info("Step 1: Creating %d group conditions...", len(group_heads))
    group_condition_map = {}
    for idx, group_id in enumerate(group_heads, 1):
        condition_id = _create_condition_sql(
            cr, group_id, default_pricelist_id, target_company_id
        )
        if condition_id:
            group_condition_map[group_id] = condition_id
            _set_condition_property(
                cr, group_id, condition_id, field_id, target_company_id
            )
            if idx % 50 == 0:
                _logger.info("  [%d/%d] groups processed...", idx, len(group_heads))
    _logger.info("Step 1 complete: %d group conditions.", len(group_condition_map))
    return group_condition_map


def _assign_group_members(
    cr, group_heads, group_condition_map, field_id, target_company_id
):
    """Step 2: Assign group condition to members."""
    _logger.info("Step 2: Assigning conditions to group members...")
    member_count = 0
    for group_id, condition_id in group_condition_map.items():
        for member_id in group_heads.get(group_id, []):
            _set_condition_property(
                cr, member_id, condition_id, field_id, target_company_id
            )
            member_count += 1
    _logger.info("Step 2 complete: %d members inherited.", member_count)
    return member_count


def _create_individual_conditions(
    cr, individuals, default_pricelist_id, field_id, target_company_id
):
    """Step 3: Create conditions for partners without group."""
    _logger.info("Step 3: Creating %d individual conditions...", len(individuals))
    for idx, partner_id in enumerate(individuals, 1):
        condition_id = _create_condition_sql(
            cr, partner_id, default_pricelist_id, target_company_id
        )
        if condition_id:
            _set_condition_property(
                cr, partner_id, condition_id, field_id, target_company_id
            )
        if idx % 100 == 0:
            _logger.info("  [%d/%d] individuals processed...", idx, len(individuals))
    _logger.info("Step 3 complete: %d individual conditions.", len(individuals))


# ``ir.property`` lookup scoped by company: prefer company-specific value,
# fall back to the company-less (global) one. Same shape for all four
# partner property fields the migration copies.
_SCOPED_PROPERTY_LOOKUP = """
    SELECT value_reference FROM ir_property
    WHERE res_id = CONCAT('res.partner,', %s::text)
      AND name = %s
      AND value_reference IS NOT NULL
      AND (company_id = %s OR company_id IS NULL)
    ORDER BY company_id NULLS LAST
    LIMIT 1
"""


def _create_condition_sql(cr, partner_id, default_pricelist_id, target_company_id):
    """Create a condition via SQL, copying partner property fields.

    Every ``ir.property`` read is scoped to ``target_company_id`` so a
    partner preconfigured in multiple companies doesn't end up with a
    ``payment_mode_id`` of one company attached to a condition of
    another — the exact bug Felipe hit in devel.
    """
    cr.execute(
        _SCOPED_PROPERTY_LOOKUP,
        (partner_id, "property_product_pricelist", target_company_id),
    )
    row = cr.fetchone()
    pricelist_id = _ref_to_id(row[0]) if row else default_pricelist_id

    cr.execute(
        _SCOPED_PROPERTY_LOOKUP,
        (partner_id, "property_payment_term_id", target_company_id),
    )
    row = cr.fetchone()
    payment_term_id = _ref_to_id(row[0]) if row else None

    cr.execute(
        _SCOPED_PROPERTY_LOOKUP,
        (partner_id, "customer_payment_mode_id", target_company_id),
    )
    row = cr.fetchone()
    payment_mode_id = _ref_to_id(row[0]) if row else None

    cr.execute(
        _SCOPED_PROPERTY_LOOKUP,
        (partner_id, "property_delivery_carrier_id", target_company_id),
    )
    row = cr.fetchone()
    delivery_carrier_id = _ref_to_id(row[0]) if row else None

    # Read incoterm and punctuality (regular columns, not ir.property)
    cr.execute(
        """
        SELECT sale_incoterm_id, punctuality_discount
        FROM res_partner WHERE id = %s
        """,
        (partner_id,),
    )
    row = cr.fetchone()
    incoterm_id = row[0] if row else None
    punctuality = row[1] if row and row[1] else 0.0

    cr.execute(
        """
        INSERT INTO partner_commercial_condition
            (partner_id, company_id, pricelist_id, payment_term_id,
             payment_mode_id, delivery_carrier_id, incoterm_id,
             contractual_return, discount_display,
             create_uid, create_date, write_uid, write_date)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, 'show_discounts',
             1, NOW(), 1, NOW())
        ON CONFLICT (partner_id, company_id) DO NOTHING
        RETURNING id
        """,
        (
            partner_id,
            target_company_id,
            pricelist_id or default_pricelist_id,
            payment_term_id,
            payment_mode_id,
            delivery_carrier_id,
            incoterm_id,
            punctuality,
        ),
    )
    result = cr.fetchone()
    return result[0] if result else None


def _set_condition_property(cr, partner_id, condition_id, field_id, target_company_id):
    """Set commercial_condition_id via ir.property (avoids ORM recompute)."""
    cr.execute(
        """
        INSERT INTO ir_property
            (name, type, fields_id, company_id, res_id, value_reference)
        VALUES
            ('commercial_condition_id', 'many2one', %s, %s,
             CONCAT('res.partner,', %s::text),
             CONCAT('partner.commercial.condition,', %s::text))
        ON CONFLICT DO NOTHING
        """,
        (field_id, target_company_id, partner_id, condition_id),
    )


def _ref_to_id(ref):
    """Extract integer ID from ir.property value_reference like 'model,123'."""
    if not ref:
        return None
    try:
        return int(ref.split(",")[1])
    except (IndexError, ValueError):
        return None

# tr_sales_rep_access — AGENTS

External sales rep access. Foundation PR of the roadmap:
`conversas_bots/plano_sales_rep_access.md`.

## Purpose

Security foundation for giving external sales reps access to the Odoo backend. Focuses
on group isolation, snapshot-based record rules and RPC-level export blocking. Feature
modules (catalog, change_request, chatter, print block) build on top of this in later
PRs.

## Key decisions (PR 1)

- **DA-2**: `group_sales_rep_external` implies only `base.group_user`. Never extend
  Sales/Account stock groups. Adding the rep to `sales_team.group_sale_salesman`,
  `account.group_account_invoice` or `account.group_account_readonly` would leak
  orders/invoices without a responsible, by the default record rules of those modules.
- **DA-6**: `sales_rep_partner_id` snapshot on `sale.order` and `account.move`. Record
  rules for orders/invoices/lines use the snapshot, not the customer's current
  `agent_ids`. This preserves history when the customer changes commercial agent.
- **DA-7**: the real price/discount lock is in `tr_commercial_policy` (write-level), not
  `engenere_restrict_saleprice_change` (onchange only). Don't treat the latter as
  security.
- **DA-7**: exporting data is blocked both by omitting the rep from
  `base.group_allow_export` and by overriding `models.BaseModel.export_data` (defense in
  depth, server-side).

## Fields

- `sale.order.sales_rep_partner_id`: Many2one `res.partner`,
  `domain=[('agent', '=', True)]`, `copy=False`, `index=True`, `readonly=True`.
  Populated in `create` from `partner.agent_ids` (single value due to the
  `tr_commercial_policy` constraint), and re-synced in `write` when `partner_id` changes
  while `state == 'draft'`. Immutable after draft.
- `account.move.sales_rep_partner_id`: same type, propagated from the sales order by the
  overridden `_prepare_invoice`.

## Record rules

- `res.partner`: two rules — a per-group open rule (`[(1,'=',1)]`) that broadens the OR
  across group rules, plus a **global rule with a conditional `user.has_group(...)`**
  domain that narrows to
  `['|', ('commercial_partner_id.agent_ids', 'in', [user.partner_id.id]), ('id', '=', user.partner_id.id)]`
  when the user is a rep and is a no-op `(1,'=',1)` otherwise. This pattern is only
  needed here because `res_partner_rule_private_employee` from `base.group_user` is
  permissive and combines by OR with any per-group rule; global rules combine by AND and
  let us narrow scope without affecting non-rep users.
- `sale.order`, `sale.order.line`, `account.move`, `account.move.line`: **single
  per-group rule** filtering by the `sales_rep_partner_id` snapshot. No open broadener
  and no global conditional are needed here because the core "personal" rules
  (`sale_order_personal_rule`, etc.) belong to `sales_team.group_sale_salesman`, which
  the rep group does not imply.

Write/unlink restriction past draft for `sale.order` and `sale.order.line` is enforced
in Python (`_sales_rep_check_rep_can_edit` in `models/sale_order.py` and
`models/sale_order_line.py`), not via a state-based record rule. `sale_stock` performs
legitimate internal writes on confirmed orders that a state-based rule would incorrectly
block; the Python gate short-circuits only for rep users.

## Export

- Add-on group `base.group_allow_export` is NOT implied by the rep group. Core already
  blocks `export_data()` without it.
- Defense in depth: `Base.export_data` raises `AccessError` for the rep group (see
  `models/base.py`).

## Catalog by agent (PR 2)

- `res.partner.allowed_category_ids` and `excluded_category_ids`: Many2many on
  `product.category`, declared with field-level
  `groups="tr_commercial_policy.group_sales_manager"` so rep users cannot read/write
  them via RPC (ORM filters the field out of `fields_get`, `read`, `write`).
- `_get_visible_category_ids()` (on partner): computed on-the-fly as
  `descendants(allowed) − descendants(excluded)`. Returns a `product.category`
  recordset; callers use `.ids` at the domain edge.
- `res.config.settings.tr_sales_rep_default_category_ids`: M2M persisted manually via
  `get_values()` / `set_values()` in `ir.config_parameter` key
  `tr_sales_rep_access.tr_sales_rep_default_category_ids` (CSV of ids). The template
  `config_parameter=` does not support Many2many, hence the manual serialization. The
  field name deliberately avoids the reserved `default_` prefix, which Odoo's settings
  classifier pairs with `ir.default` and does not support Many2many.
- `res.partner.create()` override: when `agent=True` and caller did not pass
  `allowed_category_ids`, fills it with the default configured in settings. Stale IDs
  (deleted categories) are silently ignored.
- `product.template._search()` / `product.product._search()`: when the caller is a rep,
  `AND` the domain with `('categ_id', 'in', visible_ids)`. Uses `in` (not `child_of`)
  because `visible_ids` already contains every allowed descendant minus excluded
  subtrees — `child_of` would re-expand from the roots and pull excluded categories
  back. Fail-safe empty returns `[('id', '=', 0)]`. Non-rep users are unaffected. This
  covers dropdowns, list, kanban, "Search More" and `name_search` via the delegation
  chain in the core.
- `sale.order.line` `@api.constrains("product_id")`: blocks `create`/`write` with a
  product outside the rep's catalog. Catches RPC/import/load paths that bypass
  `_search`.

## Draft → Active workflow for new customers (PR 3)

- `res.partner.create` override extension: when the acting user is in the rep group and
  the partner has no `parent_id` (i.e., it is a new commercial), the override first
  handles `agent_ids` and then forces `stage_id` to `partner_stage.partner_stage_draft`
  **regardless of any explicit value** in `vals`. On `agent_ids`:
  - if the caller did not pass the key, **auto-populate** with `env.user.partner_id`
    (the acting rep). Required because the project does not install
    `sale_commission_agent_restrict` / `trento_commission_agent_restrict`; without it,
    `agent_ids` would stay empty and the tier_definition (which demands
    `agent_ids != False`) would never fire, leaving the customer stuck in Draft.
  - if the caller passed the key, **validate** that the x2many commands resolve to
    exactly `{env.user.partner_id}` — the whitelist accepts the three canonical forms
    `(6, 0, [rep])`, `(4, rep, 0)` and the legacy `(4, rep)`, with a normalization step
    to tolerate JSON-RPC lists-of-lists. Anything else (empty replace, clear, foreign
    id, multiple commands) raises `ValidationError` so the RPC/import bypass attempt is
    visible and auditable. Business rule: a customer created by a rep **belongs to that
    rep**; there is no legitimate case for assigning another agent on create. Forcing
    Draft blocks the parallel bypass via RPC/import trying to send `stage_id=active`
    alongside the auto-populated agent.
- After the `super().create()` call, the override invokes `request_validation()` on
  rep-created commercials that match the tier domain, so the reviewer queue shows the
  new customer right away.
- `data/tier_definition.xml` declares the tier:
  - model `res.partner`
  - `review_type = group`, `reviewer_group_id = group_sales_manager`
  - `definition_type = domain`,
    `definition_domain = [('state', '=', 'draft'), ('agent_ids', '!=', False), ('parent_id', '=', False)]`
  - `notify_on_create = True`
- `sale.order` is guarded in two places:
  - `@api.constrains('partner_id')` rejects an order whose
    `partner_id.commercial_partner_id.state != 'confirmed'`. The commercial-partner
    check blocks a rep from selling through a child contact of a Draft customer.
  - `action_confirm()` repeats the same check so a customer that was Active at create
    time but was later demoted to Draft still blocks confirmation.
- Child contacts are NOT forced to Draft and do NOT trigger the tier. They use the
  system default stage (Active). Protection against selling to an unapproved commercial
  comes from the `commercial_partner_id` check, not from their own stage.

## Out of scope for this PR (planned in later PRs)

- Catalog restriction by category on agent → PR 2 (implemented, see above).
- Partner Draft workflow → PR 3 (implemented, see above).
- `tr.partner.change.request` model + wizard → PR 4.
- Rep-facing partner views → PR 5.
- Tier + `tr_rep_notes` + print block override on sale.order → PR 6.
- Server-side blocks on `eng_partner_sales_info`, `sale_order_line_price_history`,
  `sale_last_price_info`, `tr_pricelist_report` → PR 7.
- Chatter restriction (RPC + fallback overrides) → PR 8.
- Stock invisibility → PR 9.
- Final translation/docs → PR 10.

## Residual notes (tracked for later PRs)

- `res.partner` still allows `create/write` via this module's rule. The auto-populate of
  `agent_ids` at partner `create` for rep users is now handled by this module's own
  override (see "Draft → Active workflow" above) — originally planned to come from
  `sale_commission_agent_restrict`, but that module is not in the project's dependency
  chain.
- `server_action_mass_edit`: wizard ACL is at `base.group_user`, no guaranteed block by
  "not joining a group". Handled in a later PR once the real execution path is
  validated.
- Existing orders/invoices created before install have `sales_rep_partner_id = NULL`;
  they will be invisible to reps. Expected behavior on a devel-only module.

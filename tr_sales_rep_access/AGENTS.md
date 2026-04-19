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

## Out of scope for this PR (planned in later PRs)

- Catalog restriction by category on agent → PR 2.
- Partner Draft workflow → PR 3.
- `tr.partner.change.request` model + wizard → PR 4.
- Rep-facing partner views → PR 5.
- Tier + `tr_rep_notes` + print block override on sale.order → PR 6.
- Server-side blocks on `eng_partner_sales_info`, `sale_order_line_price_history`,
  `sale_last_price_info`, `tr_pricelist_report` → PR 7.
- Chatter restriction (RPC + fallback overrides) → PR 8.
- Stock invisibility → PR 9.
- Final translation/docs → PR 10.

## Residual notes (tracked for later PRs)

- `res.partner` still allows `create/write` via this module's rule. If a real rep is
  ever added to the group before the workflow PR, writes into the partner need the
  auto-populate of `agent_ids` at partner `create`, as implemented by
  `sale_commission_agent_restrict`. Not blocking because PR 1 is staging.
- `server_action_mass_edit`: wizard ACL is at `base.group_user`, no guaranteed block by
  "not joining a group". Handled in a later PR once the real execution path is
  validated.
- Existing orders/invoices created before install have `sales_rep_partner_id = NULL`;
  they will be invisible to reps. Expected behavior on a devel-only module.

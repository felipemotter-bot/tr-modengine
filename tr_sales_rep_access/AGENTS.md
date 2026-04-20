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

## Change request (PR 4a)

`tr.partner.change.request` (+ `tr.partner.change.request.line`): rep-facing request
object that a rep opens to update a customer's cadastral fields or add a new child
contact, subject to Sales Manager approval via `tier.validation`. The 4a ships the
structure; the wizard that propagates approved updates to the customer's open orders and
the server-side block on rep writing `res.partner` fields directly live in 4b.

- **Whitelist**: `models/tr_partner_change_request_line.py` defines
  `EDITABLE_PARTNER_FIELDS = ('phone', 'mobile', 'email', 'street', 'street2', 'city', 'zip', 'state_id', 'country_id')`.
  The line's `field_id` (M2O to `ir.model.fields`) uses a domain lambda pointing to this
  tuple, plus a `@api.constrains` that revalidates the whitelist server-side (RPC/import
  paths that ignore the view domain).
- **Typed payload**: lines carry either `new_value_char` (for char fields) or
  `new_value_reference` (for many2one, with the selection restricted to the two
  legitimate comodels: `res.country` and `res.country.state`). A `@api.constrains`
  checks that the payload matches `field_id.ttype` and `field_id.relation`.
- **Request types**: `field_update` (uses `line_ids`) and `new_child` (inline payload
  `new_child_name/email/phone/mobile/function/type`; one request = one new contact).
  Constraint on the header rejects mixed payload (lines + child data) or missing
  required pieces for each type.
- **Tier**: `data/tier_definition.xml` declares `tier_def_partner_change_request`
  (`review_type=group`, reviewer `group_sales_manager`, domain
  `state=pending && sales_rep_partner_id != False`, `notify_on_create=True`).
  `has_comment` MUST stay False — `action_approve` / `action_reject` do not propagate
  the wizard action the mixin returns when `has_comment=True`, and
  `_raise_if_comment_required` fails fast in Python if it is turned on.
- **State machine**: `pending → approved | rejected | cancelled`. Mixin attributes
  overridden to `_state_from=['pending']`, `_state_to=['approved']`,
  `_cancel_state='cancelled'`. `action_reject` uses the mixin's `reject_tier()` plus
  `with_context(skip_validation_check=True)` to clear the "Write under validation" guard
  — `rejected` is not in `_state_to + [_cancel_- state]`.
- **Authorship invariants**: on create, when the acting user is in the rep group,
  `requested_by` and `sales_rep_partner_id` are **forced** to `env.user` /
  `env.user.partner_id` regardless of what comes in vals — closes the RPC/import forgery
  path for the audit trail and the per-group record rule.
- **Direct state-write bypass**: the `write` override rejects any change to `state`
  unless the private context flag `_tr_change_request_internal_trans- ition` is set; the
  three `action_*` methods set it explicitly, so RPC-level shortcuts like
  `with_context(skip_validation_check=True).write({'state': 'rejected'})` are blocked.
- **Line hardening**: `create` / `write` / `unlink` on the line reject if
  `request_id.state != 'pending'`. The `unlink` on the header is manager-only and only
  for `rejected/cancelled` records (audit trail).
- **Uniqueness**: `@api.constrains` enforces one `pending` request per partner, using
  `sudo().search_count(...)` because the per-group record rule would otherwise hide
  pending requests from other reps and let a rep open a second one that collides at the
  business level.
- **Terminal review cleanup**: `action_reject` and `action_cancel` call
  `_close_pending_reviews()` (sweeps reviews still `pending` via sudo), because the
  mixin's `reject_tier()` only rejects the acting user's reviews — with more than one
  tier.definition the others would be left dangling.
- **Happy path of approve**: `action_approve` validates the tier, then checks
  `validation_status == 'validated'` before touching the partner or creating the child.
  Without that check, a write to `state='approved'` blocked by the mixin would leave the
  partner already mutated.
- **Record rules**: `tr.partner.change.request` per-group rule filters by
  `sales_rep_partner_id = user.partner_id.id`. Line rule follows the parent's snapshot.
  Both are simple per-group rules — no open broadener needed because the model is new
  and has no permissive core rules.
- **Views / navigation**: form with status bar, conditional notebook (field lines vs.
  child data), `mail.thread` chatter. Two header buttons on `res.partner` form (Request
  field update / Request new contact), visible only to rep users on commercial parents
  in `confirmed` stage. Menu under `sale.sale_menu_root`.

## Direct write block on Active partners (PR 4b)

Closes the change-request loop: rep cannot write directly on a partner in Active stage;
every cadastral change goes through `tr.partner.change.request`, and the commercial
condition goes through the `tr_commercial_policy` action buttons.

- **Override** of `res.partner.write` (`_sales_rep_should_block_active_write`): rejects
  with `AccessError` when `env.user.has_group(group_sales_rep_external)` and the write
  touches any field outside `REP_ACTIVE_WRITE_ALLOWLIST` and the recordset contains at
  least one partner with `state == 'confirmed'`.
- **Allowlist**: hard-coded tuple in `models/res_partner.py` listing the `mail.thread`
  and `mail.activity.mixin` fields (`message_ids`, `message_follower_ids`,
  `activity_ids`, etc.). Chatter and scheduled activities stay open — they are not
  business data. Choice was hard-coded over mro detection for auditability; add new
  entries when a future Odoo bump introduces new mail/activity fields.
- **Draft stage stays fully editable**: PR 3 create flow is untouched. Rep creates
  partner → nasce Draft → edits freely → manager approves tier → state flips to Active →
  guard engages.
- **Internal writes bypass via sudo**: `_apply_field_update` and `_create_child` in
  `tr.partner.change.request` already use `.sudo()`; `env.user` becomes SUPERUSER and
  `has_group(REP_GROUP)` returns False, so the guard is a no-op for them.
- **`tr_commercial_policy` action buttons use `.sudo()` on the partner write**. Three
  methods adjusted in `tr_commercial_policy/models/res_partner.py`:
  `action_create_commercial_condition`, `action_create_override_condition` and
  `action_remove_override_condition`. The module owns the `commercial_condition_id`
  field and gates all legitimate writes behind these action buttons; `.sudo()` lets the
  writes pass the PR 4b guard without exposing the field on the allowlist (which would
  let a rep set it via RPC, bypassing the action logic).
- **Child contacts (intentional limitation)**: a child partner inherits
  `state == 'confirmed'` from `partner_stage` defaults, so direct edits by a rep on an
  existing Active child also hit the guard. There is no workflow for editing existing
  children today (rep only adds new ones via `new_child`). If a real need shows up, a
  future PR will extend `tr.partner.change.request` with a `field_update_child` variant
  — we keep the "everything via change_request" principle instead of opening an
  exception.

## Server-side hide of non-operational sensitive fields (PR 5)

Rep cannot read or write a handful of sensitive fields on any partner, regardless of
stage. These are fields that don't participate in the rep's operational flow (sale order
creation, fiscal compute, etc.), so blocking them server-side via `groups=!rep` is safe.

- **partner_capital**: `capital_amount`, `capital_currency_id`, `turnover_range_id`,
  `turnover_amount`, `company_size`.
- **l10n_br_sped_base**: `is_accountant`, `crc_code`, `crc_state_id`.
- **l10n_br_account_withholding**: `wh_cityhall`.
- **l10n_br_hr**: `union_entity_code`.

Each field is redeclared in `models/res_partner.py` with
`groups="!tr_sales_rep_access.group_sales_rep_external"`. The ORM filters them out of
`fields_get`, `read` and `write` for reps (AccessError on explicit access, silent filter
on `fields_get`). The modules above are added to `depends` for the same reason: the
redeclaration needs the original field to exist at registry build time.

**Deferred to future PRs** because of an XPath limitation in Odoo 16:

- **View-only hide** of fiscal operational fields (`vat`, `tax_framework`,
  `fiscal_profile_id`, `ind_ie_dest`, `ind_final`, `l10n_br_ie_code`, `l10n_br_im_code`,
  `rntrc_code`, `legal_nature_id`, `cnae_*`, `is_public_entity`, `public_entity_type`,
  `nif_motive_absence`) — these appear in multiple subviews of the partner form (main
  sheet, child_ids subtree, kanban embed) and `xpath position="attributes"` only applies
  to the first match, leaving others exposed. A dedicated PR needs either N targeted
  xpaths or an override of the upstream `l10n_br_fiscal` onchanges running under `sudo`
  (to promote those fields to server-side hide without breaking fiscal flow).
- **Readonly-active** UX on cadastral fields (`phone`, `mobile`, `email`, `street`,
  `street2`, `city`, `zip`, `state_id`, `country_id`, `name`) — same XPath limitation,
  and overriding `attrs` on fields that already ship `required`/`invisible` upstream
  clobbers their own rules. The real write protection is already in place (PR 4b's
  `res.partner.write` guard), so moving this to a dedicated PR is acceptable.

## Rep notes on sale order (PR 6a)

- Field `sale.order.tr_rep_notes`: `fields.Text`, no `tracking`, no `groups=`, no state
  `attrs`. Free-text scratchpad for the rep to coordinate with the sales manager during
  quotation negotiation. Not printed on the PDF.
- Location on the form: inside the "Other Info" page, as a new group
  `name="sales_rep" string="Sales Rep"` inserted after the core's `sale_info` group.
  Field carries `nolabel="1"` + a placeholder so the group title acts as label.
- Write policy: the pre-existing PR 1 guard `_sales_rep_check_rep_can_edit` still
  applies — rep edits `tr_rep_notes` only while the order is in `draft`; after confirm
  the manager holds the pen. No whitelist exception for the notes field (Felipe
  2026-04-19). This keeps PR 6a's change surface trivial while respecting the PR 1 "rep
  cannot modify non-draft orders" invariant.

## Out of scope for this PR (planned in later PRs)

- Catalog restriction by category on agent → PR 2 (implemented, see above).
- Partner Draft workflow → PR 3 (implemented, see above).
- `tr.partner.change.request` model + tier + view → PR 4a (implemented, see above).
- Direct-write block on Active partners + `tr_commercial_policy` sudo → PR 4b
  (implemented, see above).
- Server-side hide of non-operational sensitive partner fields → PR 5 (implemented, see
  above).
- Readonly-active UX for rep contact fields on partner form → PR 5b (implemented — scope
  reduced to `name`/`phone`/`mobile`/`email` after discovering that the address block
  lives in `l10n_br_base.l10n_br_base_res_partner_address` primary view, outside the
  base form's inherit chain). PR 4b's write guard still blocks writes on Active partners
  server-side.
- Fiscal-field hide (PR 5c) and address-block readonly-active (PR 5d): abandoned. The PR
  4b server-side guard already blocks writes; remaining gaps are cosmetic.
- `tr_rep_notes` free-text field on `sale.order` → PR 6a (see below).
- Tier "Conferente" + print block override on sale.order → PR 6b (planned).
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

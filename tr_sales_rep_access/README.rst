================
Sales Rep Access
================

.. |badge1| image:: https://img.shields.io/badge/licence-AGPL--3-blue.svg
    :target: http://www.gnu.org/licenses/agpl-3.0-standalone.html
    :alt: License: AGPL-3

|badge1|

External sales rep access module. Introduces an isolated security
group (``group_sales_rep_external``) and snapshot-based record rules
so that external reps only see the partners, quotations and invoices
within their own scope, while preserving history when the commercial
agent of a customer changes.

**Table of contents**

.. contents::
   :local:

Overview
========

This module is the foundation for giving external sales
representatives access to the Odoo backend. It focuses strictly on
security primitives:

- A dedicated group ``group_sales_rep_external`` that implies only
  ``base.group_user`` (no Sales/Account defaults to avoid accidental
  access to records without responsible).
- A snapshot field ``sales_rep_partner_id`` on ``sale.order`` and
  ``account.move`` that captures the rep responsible for the
  document at creation and is propagated from quotation to invoice
  via ``_prepare_invoice``.
- Record rules that apply the snapshot (and the partner's
  ``agent_ids`` M2M) to filter visibility. ``res.partner`` uses a
  per-group open rule plus a global rule with a conditional
  ``user.has_group(...)`` domain to neutralise the permissive core
  ``res_partner_rule_private_employee``; ``sale.order``,
  ``sale.order.line``, ``account.move`` and ``account.move.line``
  use a simple per-group rule by snapshot.
- Python overrides in ``sale.order`` and ``sale.order.line``
  blocking ``write``/``unlink`` past draft for rep users (not a
  state-based rule, because ``sale_stock`` performs legitimate
  internal writes on confirmed orders that a rule would wrongly
  block).
- A defense-in-depth override of ``models.BaseModel.export_data``
  that raises ``AccessError`` for rep users.

Everything else (catalog restriction, partner-draft workflow,
change-request model, tier print block, chatter restriction,
stock invisibility, etc.) is implemented in later PRs of the
``tr_sales_rep_access`` roadmap.

Configuration
=============

1. Install the module.
2. Add the user that represents the external rep to the group
   **Sales Rep External**. The user's ``partner_id`` must be a
   partner with ``agent = True`` (i.e., a registered commission
   agent).
3. For existing customers that should be visible to the rep, ensure
   ``partner.agent_ids`` includes the rep's partner.
4. New sales orders created from that customer will automatically
   record the rep as ``sales_rep_partner_id`` snapshot.

Usage
=====

After configuration, a rep logging in will only see:

- Their own partner and the partners whose
  ``commercial_partner_id.agent_ids`` includes them.
- Sales orders where ``sales_rep_partner_id`` is them.
- Sales order lines belonging to those orders.
- Invoices and invoice lines carrying the same snapshot.

Write, create and unlink on sales orders and lines by rep users are
restricted to ``state == 'draft'`` through Python overrides on the
models. Attempting to export any model via RPC raises ``AccessError``.

Known limitations (addressed in later PRs)
==========================================

- No catalog restriction yet (rep sees any product).
- No tier validation on new customer creation yet.
- Chatter is not restricted yet (the group can read/post messages
  within its record scope).
- ``tr_pricelist_report`` print button on partner is not gated by
  group yet.
- Master data kept pristine: existing sales orders created before
  installation have ``sales_rep_partner_id`` NULL and are therefore
  invisible to reps. Expected on a devel-only module install.

Bug Tracker
===========

Bugs and enhancements are tracked on the internal repository.

Credits
=======

* Engenere - https://www.engenere.one

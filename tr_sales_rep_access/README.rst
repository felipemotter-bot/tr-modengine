================
Sales Rep Access
================

.. |badge1| image:: https://img.shields.io/badge/licence-AGPL--3-blue.svg
    :target: http://www.gnu.org/licenses/agpl-3.0-standalone.html
    :alt: License: AGPL-3

|badge1|

Complete security module for external sales representatives. Introduces
an isolated security group, snapshot-based record rules, catalog
restriction, partner-draft approval workflow, change-request model,
chatter restriction, stock invisibility and tier-based order
conference — so that external reps only see and operate their own
scope while internal users are unaffected.

**Table of contents**

.. contents::
   :local:

Overview
========

This module gives external sales representatives controlled access to
the Odoo backend. The full feature set, delivered across PRs 1–9:

**Security foundation (PR 1)**

- A dedicated group ``group_sales_rep_external`` that implies only
  ``base.group_user`` (no Sales/Account defaults).
- A snapshot field ``sales_rep_partner_id`` on ``sale.order`` and
  ``account.move``, propagated to invoices via ``_prepare_invoice``.
- Record rules filtering partners, orders, order lines, invoices and
  invoice lines by snapshot / ``agent_ids``.
- Python overrides blocking ``write``/``unlink`` past draft for rep
  users; defense-in-depth ``export_data`` override.

**Catalog restriction (PR 2)**

- Per-rep ``allowed_category_ids`` / ``excluded_category_ids`` on
  ``res.partner``; ``product.template``/``product.product._search``
  filtered to the rep's allowed categories; ``sale.order.line``
  constraint blocking out-of-catalog products on RPC paths.

**Partner draft workflow (PR 3)**

- New customers created by a rep start in Draft stage and must be
  approved by Sales Manager via ``base_tier_validation`` before
  orders can be confirmed.

**Change request (PR 4a/4b)**

- ``tr.partner.change.request`` model for rep-initiated partner
  updates, subject to tier approval. Direct writes by reps on Active
  partners are blocked server-side; ``tr_commercial_policy`` action
  buttons use ``sudo()`` to bypass the guard legitimately.

**Sensitive field hide (PR 5/5b)**

- Capital, BR-accounting, HR and union fields on ``res.partner``
  redeclared with ``groups=!rep``. Readonly-active UX on contact
  fields (``name``, ``phone``, ``mobile``, ``email``).

**Rep notes + conference tier (PR 6a/6b)**

- ``tr_rep_notes`` scratchpad on ``sale.order``; conference tier
  (``group_sales_rep_checker``) with print-block override that allows
  printing while only the conference tier is pending.

**Companion module blocks (PR 7)**

- Server-side hide of sales-analysis fields (``eng_partner_sales_info``),
  price-history widgets (``sale_order_line_price_history``) and
  pricelist-print action (``tr_pricelist_report``).

**Chatter restriction (PR 8)**

- ``message_ids``, ``message_follower_ids`` and ``website_message_ids``
  redeclared with ``groups=!rep`` on ``sale.order``, ``res.partner``
  and ``account.move``. Rep writes use ``mail_notrack=True`` context.
  View-level ``oe_chatter`` div hidden as defense in depth.

**Stock invisibility (PR 9)**

- ``qty_available``, ``virtual_available``, ``incoming_qty``,
  ``outgoing_qty`` redeclared with ``groups=!rep`` on
  ``product.template`` and ``product.product``. ``display_qty_widget``
  blocked at field-level, causing the stock forecast widget to be
  invisible for reps.

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

Bug Tracker
===========

Bugs and enhancements are tracked on the internal repository.

Credits
=======

* Engenere - https://www.engenere.one

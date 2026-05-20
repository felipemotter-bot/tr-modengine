======================
Sale Delivery Planning
======================

.. |badge1| image:: https://img.shields.io/badge/licence-AGPL--3-blue.svg
    :target: http://www.gnu.org/licenses/agpl-3.0-standalone.html
    :alt: License: AGPL-3

|badge1|

Foundation module for planning sale delivery dates on the outgoing
``stock.picking`` of a sales order, with a dedicated planner group and
defense-in-depth blindage so native lateness views do not show noise
for not-yet-planned sale deliveries.

**Table of contents**

.. contents::
   :local:

Architecture
============

Two new fields on ``stock.picking``:

- ``planned_delivery_date`` (Date, nullable, indexed): planner's source
  of truth. When set, an ``inverse``-style sync writes
  ``scheduled_date`` at end-of-day in the user's timezone.
- ``is_delivery_planned`` (Boolean, computed+stored+searchable): equals
  ``bool(planned_delivery_date)``. Available to any view filter for
  centralized blindage of native lateness widgets.
- ``is_sale_delivery_planning_applicable`` (Boolean,
  computed+stored+searchable): true only for outgoing sale deliveries
  with location ``internal → customer``. Excludes purchases, internal
  transfers, dropshipping, return-from-customer and intermediate
  pick/pack steps.

A new group ``group_delivery_planner`` ("Delivery Planner") is the
only role allowed to write ``planned_delivery_date``. The guard is
enforced server-side in ``create``/``write`` with a bypass context
``bypass_delivery_planning_acl`` used by the automatic initialization
inside ``sale.order._action_confirm``.

Defense in layers against ``scheduled_date`` leakage
====================================================

Native Odoo widgets and queries do not know about
``planned_delivery_date`` and would happily display every sale picking
without a planned date as "Late" (because ``scheduled_date`` defaults
to ``now()``). This module patches the four central computes / counts
so that sale-applicable pickings without a planned date are treated as
"not yet operational" instead of "late":

1. ``stock.picking._compute_has_deadline_issue`` — cleared.
2. ``stock.picking._compute_products_availability`` — cleared.
3. ``stock.picking.type._compute_picking_count`` — ``count_picking_late``
   excludes applicable+unplanned pickings.
4. Native search view filters ``Late`` and ``Planning Issues`` extended
   in ``views/stock_picking_views.xml`` to skip the same scope.

All four overrides apply **only** when
``is_sale_delivery_planning_applicable AND NOT is_delivery_planned``.
Purchases, transfers, returns and dropshipping retain native behavior.

Known gaps (deferred)
=====================

- ``stock_picking_late_activity`` (OCA): if installed, its
  ``_cron_late_picking_activity`` still creates activities for
  applicable+unplanned pickings. Blindage of this cron is intentionally
  out of scope of this foundation PR to avoid a hard depends. Will be
  addressed in a follow-up glue module if/when the OCA module is in
  operational use.
- Portal of pickings, delivery slip report and OCA
  ``stock_picking_group_by_partner_by_carrier_by_date`` may still show
  raw ``scheduled_date``. Treat reactively if it becomes a real issue.
- Rich UI (kanbans "No date" / "By day", calendar view, needs reports
  pivot/list/grouped) is delivered in subsequent PRs.

Usage
=====

After install:

1. Assign one or two users to the new "Delivery Planner" group in
   Settings → Users.
2. Sales reps continue creating cotações as before. They may fill the
   ``commitment_date`` on the sale order as an initial promise.
3. When the order is confirmed, ``planned_delivery_date`` is
   automatically copied to the outgoing picking from
   ``commitment_date`` if present; otherwise the picking starts with
   no planned date and waits for the planner.
4. The planner opens the picking and sets ``planned_delivery_date``.
   ``scheduled_date`` is automatically synchronized to end-of-day.

Sales reps see ``Next Planned Delivery`` (``next_planned_delivery_date``)
on the sale order form, read-only, reflecting the next pending picking
that already has a planned date.

Credits
=======

Authors
~~~~~~~

- Engenere

Contributors
~~~~~~~~~~~~

- Felipe Motter Pereira <felipe@trentoquimica.com.br>

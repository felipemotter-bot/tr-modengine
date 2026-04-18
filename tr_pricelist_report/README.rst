=========================
Pricelist Report
=========================

.. |badge1| image:: https://img.shields.io/badge/licence-AGPL--3-blue.svg
    :target: http://www.gnu.org/licenses/agpl-3.0-standalone.html
    :alt: License: AGPL-3

|badge1|

Customer pricelist report driven by the commercial policy. Disables the
native pricelist print entry points (which ignore commercial conditions,
``contractual_return``, ``adjustment_factor`` and other policy inputs)
and ships a dedicated report that prints prices consistent with what the sale
order and invoice actually compute.

**Table of contents**

.. contents::
   :local:

Configuration
=============

System parameters (``Settings > Technical > Parameters > System Parameters``):

- ``tr_pricelist_report.validity_days`` (default ``30``) — default days
  added to the issue date to build the report validity ``date_end``.
- ``tr_pricelist_report.category_depth`` (default ``-2``, parent of
  the leaf) — depth used when grouping by ``product.category``.
  Accepts ``-1`` (leaf), ``-N`` (N-th ancestor, clamps to root),
  ``N >= 0`` (absolute level from root, clamps to leaf).
- ``tr_pricelist_report.group_attribute_name`` (default ``MARCA``) —
  name of the ``product.attribute`` used as the grouping axis in Mode
  A of the Complete Pricelist layout.
- ``tr_pricelist_report.history_months_back`` (default ``6``) — window
  in months the Customer History layout walks back on
  ``sale.order.line``. Set to ``0`` to disable the layout (the
  resolver short-circuits and the wizard raises a UserError on
  empty history).

Send by email
~~~~~~~~~~~~~

Ticking **Send by email** on the wizard renders the PDF, attaches it
to the commercial condition, and opens a mail composer pre-filled from
the ``Pricelist Report: Send to Customer`` mail template. You can
edit recipients, subject and body before sending; the message is
logged on the condition's chatter with the attachment. Partners
without an email address don't block — fill the email directly in the
composer.

Customer History layout
~~~~~~~~~~~~~~~~~~~~~~~

Picking ``Customer History`` as layout prints products the partner
actually bought in the ``history_months_back`` window, grouped by
category and listed variant by variant with the aggregated quantity
(in the product's default UoM) and the current price the condition
resolves today. Products archived or flagged ``sale_ok=False`` are
left out — the report is a reorder tool, not an audit log. Products
sitting under a category that was flagged with
**Exclude from general pricelists** still show up here: the customer
already paid for it once, so they must see the price for reorders.

Exclude custom categories
~~~~~~~~~~~~~~~~~~~~~~~~~

Each ``product.category`` gets the flag **Exclude from general
pricelists**. Flag a category (or any ancestor of it — the cascade is
rigid) and every product underneath is hidden from ``By Category``
and ``Complete Pricelist`` layouts. The flag is ignored by the customer
history layout, so buyers who already ordered the product still see
the price for reorders.

Upgrade in devel
~~~~~~~~~~~~~~~~

While the commercial-policy stack only runs in the ``devel`` database,
apply new defaults by **reinstalling** the module (``-i
tr_pricelist_report``) rather than ``-u``. ``noupdate="1"`` on the XML
config records and the ``post_init_hook`` that resolves
``group_attribute_id`` don't fire on plain updates.

Usage
=====

- Open a **Commercial Condition** (``Sales > Configuration > Commercial
  Conditions``) and click **Print Price List** in the header.
- Or open a **Partner** with an effective commercial condition and click
  the same button.
- Pick the target categories in the wizard, adjust validity date if needed,
  and generate the PDF.

Price calculation reuses the same chain as ``sale.order.line``
(``_get_tax_included_unit_price`` → ``calc_reference_price`` →
``calc_price_unit``) so printed prices match what orders and invoices will
compute for the same partner.

Bug Tracker
===========

Bugs are tracked on `GitHub Issues
<https://github.com/Engenere/addons-trento/issues>`_.

Credits
=======

Authors
~~~~~~~

* Engenere

Contributors
~~~~~~~~~~~~

* Felipe Motter Pereira <felipe@engenere.com>

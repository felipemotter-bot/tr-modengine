===========================
Commercial Policy
===========================

.. |badge1| image:: https://img.shields.io/badge/licence-AGPL--3-blue.svg
    :target: http://www.gnu.org/licenses/agpl-3.0-standalone.html
    :alt: License: AGPL-3

|badge1|

Sales profiles, commercial conditions and structured discount channels for
multi-company commercial policy management.

**Table of contents**

.. contents::
   :local:

Configuration
=============

1. Go to **Sales > Configuration > Sales Profiles** and create profiles
   (agent or internal) with discount rules and commission/order-value bands.
2. Assign profiles to salespersons via the partner form, sales teams, or
   the global default in system parameters.
3. Go to **Sales > Configuration > Commercial Conditions** to define
   per-customer discount conditions (cash, FOB, seller, contractual return).

Usage
=====

When creating a sale order:

- The sales profile is automatically resolved from the salesperson, team, or
  global default.
- The commercial condition is resolved from the customer or their company
  group.
- Discounts (cash, FOB, seller, extra) flow through structured channels —
  direct editing of ``price_unit`` and ``discount`` is blocked when a profile
  is active.
- Commission rates are computed dynamically from the seller discount and
  commission bands.
- Extra discounts require manager/director approval before order confirmation.
- For internal profiles, seller discount limits are dynamic based on the
  order amount and order-value bands.

Bug Tracker
===========

Bugs are tracked on `GitHub Issues <https://github.com/Engenere/addons-trento/issues>`_.

Credits
=======

Authors
~~~~~~~

* Engenere

Contributors
~~~~~~~~~~~~

* Felipe Motter Pereira <felipe@engenere.one>

Maintainers
~~~~~~~~~~~

This module is maintained by Engenere.

.. image:: https://engenere.one/logo.png
   :alt: Engenere
   :target: https://engenere.one

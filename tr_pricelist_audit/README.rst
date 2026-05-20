================
Pricelist Audit
================

Adds chatter to ``product.pricelist`` and logs a consolidated change history
of its lines (``product.pricelist.item``) on the parent pricelist.

Features
========

* Chatter (``mail.thread`` + ``mail.activity.mixin``) on ``product.pricelist``.
* Native tracking on ``name``, ``currency_id``, ``company_id``, ``active``,
  ``discount_policy``.
* Internal note posted on the parent pricelist whenever a line is created,
  modified or removed. Modified lines list field-level diffs.
* Moving a line between pricelists logs both pricelists.
* Batch operations above 20 records group into a single note per pricelist.

Notes
=====

* Only the relevant set of fields is logged on items (target, price, qty,
  validity). Formula-mode pricelists are out of scope.

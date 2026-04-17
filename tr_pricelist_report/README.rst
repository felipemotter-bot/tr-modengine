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
- ``tr_pricelist_report.category_depth`` (default ``-1``) — depth used
  when grouping by ``product.category``. ``-1`` means the leaf category.

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

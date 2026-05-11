==================
Trento Report Style
==================

Shared visual kit for Trento PDF reports — common CSS (``.tr-doc-header``,
``.tr-doc-title``, ``.tr-section-title``, ``.tr-table``) plus a few QWeb
macros (``doc_title``, ``section_title``).

Consumers ``t-call`` ``tr_report_style.report_styles`` at the top of their
``<div class="page">`` body and use the ``.tr-*`` semantic classes for
elements that are common across the Trento report family. Report-specific
stylesheets only carry rules unique to each report.

Used by:

* ``tr_pricelist_report``
* ``tr_commercial_policy`` (sales profile report)
* ``trento_report_sale`` (sale order report)

Marker
======

The kit emits a hidden marker (``class="tr-report-style-loaded"``) so tests
can assert that the kit was actually loaded by the consumer template.

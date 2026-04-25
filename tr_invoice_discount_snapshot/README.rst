=================================
Trento Invoice Discount Snapshot
=================================

.. |badge1| image:: https://img.shields.io/badge/licence-AGPL--3-blue.svg
    :target: http://www.gnu.org/licenses/agpl-3.0-standalone.html
    :alt: License: AGPL-3

|badge1|

Garante que o desconto da política comercial (``tr_cash_discount`` +
``tr_fob_discount``) propague para a linha da ``account.move`` mesmo em
empresas sem configuração fiscal — onde ``fiscal_document_line_id`` não é
populado e o compute padrão do ``l10n_br_account`` zera o ``discount`` da
linha.

Comportamento
=============

- Em linha **com** ``fiscal_document_line_id``: o documento fiscal é a fonte
  da verdade — preserva fluxo NF-e/SPED.
- Em linha **sem** ``fiscal_document_line_id``: aplica
  ``move.tr_cash_discount + move.tr_fob_discount`` do cabeçalho.
- Faturas em estado ``posted`` são imutáveis (gate de proteção contra
  recompute em massa no install).

Instalação
==========

O módulo grava um snapshot SQL de ``discount`` e ``discount_value`` antes
do recompute do ORM (``pre_init_hook``) e restaura via ``post_init_hook``
qualquer linha posted indevidamente alterada — defesa em profundidade
para dados fiscais críticos.

Escopo
======

Exclusivo da Trento. Resolve o caso de empresas-sandbox (ex: TREINAMENTO)
que não têm configuração fiscal mas precisam ver o desconto refletido em
faturas geradas a partir de pedidos com política comercial ativa.

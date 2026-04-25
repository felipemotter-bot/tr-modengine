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

- ``account.move.line.discount`` e ``account.move.line.discount_value`` são
  compute store=True a partir de ``move.tr_cash_discount + move.tr_fob_discount``
  (header) e da base atual da linha (``quantity * price_unit``). Header é
  fonte ÚNICA do percentual.
- ``l10n_br_fiscal.document.line.discount_value`` é redefinido como
  ``related("account_line_ids.discount_value")`` — fiscal LE o valor que
  a linha do account guarda. NF-e/SPED enxergam o mesmo valor que o
  usuário vê na fatura.
- Faturas em estado ``posted`` são imutáveis (gate de proteção contra
  recompute em massa no install).

Por que account é fonte e fiscal é related
==========================================

Mesmo padrão do ``engenere_account_invoice_br_discount`` (descontinuado),
que rodou em produção da Trento por anos sem incidente. Vantagens:

- Editar ``tr_cash_discount + tr_fob_discount`` no header da fatura recalcula
  ``account.move.line.discount_value``, e a NF-e vê o mesmo valor automaticamente.
- Mudar ``quantity`` ou ``price_unit`` em draft mantém o percentual estável
  (compute recalcula a base).
- Sem write-hook imperativo, sem snapshot duplicado, sem guard de recursão.

Escopo intencional
==================

O módulo **não implementa ``inverse``** em ``discount`` / ``discount_value``
e **não pretende suportar edição manual de desconto na linha como feature**.
A regra de negócio aprovada com Felipe é: edição sempre no cabeçalho do
pedido/fatura, nunca por linha. Se um caso futuro exigir edição por linha,
isso é mudança de escopo (snapshot por linha + inverse), não evolução deste
módulo.

Risco conhecido
===============

``account_line_ids`` em ``l10n_br_fiscal.document.line`` é One2many; o
``related`` escalar lê o **primeiro** registro. Em invoice grouping com
várias ``account.move.line`` por ``fiscal.document.line`` (raro/inexistente
no fluxo Trento atual), só a primeira contribui pro fiscal. Se aparecer
caso real, evolui para agregação explícita.

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

# Guia de Uso — Sales Rep Access

Módulo que dá acesso controlado ao backend Odoo para representantes comerciais externos.
Este guia é vivo: cresce a cada PR entregue do roadmap em
`conversas_bots/plano_sales_rep_access.md`.

Status atual: **PR 2 — Catálogo por agente** (+ PR 1 fundação mergeada).

---

## Índice

1. [Conceitos Principais](#1-conceitos-principais)
2. [Quem é o representante externo](#2-quem-é-o-representante-externo)
3. [Configuração inicial](#3-configuração-inicial)
4. [O que o rep enxerga (PR 1)](#4-o-que-o-rep-enxerga-pr-1)
5. [O que o rep NÃO enxerga (PR 1)](#5-o-que-o-rep-não-enxerga-pr-1)
6. [Fluxo de lançamento de pedido (PR 1)](#6-fluxo-de-lançamento-de-pedido-pr-1)
7. [Bloqueios server-side (PR 1)](#7-bloqueios-server-side-pr-1)
8. [Troca de agente e histórico](#8-troca-de-agente-e-histórico)
9. [Catálogo por agente (PR 2)](#9-catálogo-por-agente-pr-2)
10. [Roadmap — o que vem nas próximas PRs](#10-roadmap)
11. [Solução de problemas](#11-solução-de-problemas)

---

## 1. Conceitos principais

### Representante externo

Pessoa que **não é funcionário direto da Trento**, mas lança pedidos no sistema pelos
clientes sob sua carteira. Tipicamente um agente comercial com comissão por banda de
desconto (perfil agente do `tr_commercial_policy`).

### Grupo de acesso

`tr_sales_rep_access.group_sales_rep_external` — grupo dedicado, implica apenas
`base.group_user`. **Nunca** adicionar o rep a grupos de Sales
(`sales_team.group_sale_salesman`), Contabilidade (`account.group_account_invoice`) ou
`base.group_allow_export`.

### Snapshot do representante

Campo `sales_rep_partner_id` gravado automaticamente em `sale.order` e `account.move` no
momento da criação. É um **fotografia** do agente responsável pelo pedido, e não
acompanha alterações posteriores no cadastro do cliente — o histórico fica preservado
mesmo se o cliente trocar de agente.

### Record rules — duas estratégias

**`res.partner` — per-group aberta + global conditional.** O core tem
`res_partner_rule_private_employee` como rule de `base.group_user`, que libera ver quase
todos os parceiros. Como o rep group implica `base.group_user` transitivamente, uma rule
per-group simples seria combinada por OR com essa rule permissiva e perderia o escopo.
Então:

1. **Rule per-group aberta** `[(1, '=', 1)]` no grupo do rep — amplia o OR entre rules
   de grupo, evitando que rules mais restritivas de outros grupos limitem o rep.
2. **Rule global** com domain condicional
   `[restrição] if user.has_group('...') else [(1, '=', 1)]` — aplica AND. Quando o user
   é rep, narra ao escopo; caso contrário vira no-op. Garante que o filtro só atinge
   reps.

**`sale.order`, `sale.order.line`, `account.move`, `account.move.line` — rule per-group
simples.** As rules "personal" do core (`sale_order_personal_rule`, etc.) pertencem a
`sales_team.group_sale_salesman`, que o rep group **não** implica. Logo uma única rule
per-group filtrando pelo snapshot basta. **Nenhum open broadener e nenhum global
conditional são necessários nesses modelos.**

### Bloqueio de write/unlink pós-draft — em Python, não em rule

`sale.order` e `sale.order.line` sobrescrevem `write`/`unlink` com o helper
`_sales_rep_check_rep_can_edit`: se o user logado é do grupo rep e o order não está mais
em `draft`, levanta `AccessError`. Optou-se por Python em vez de rule state-based porque
o `sale_stock` faz writes legítimos em pedidos confirmados (`procurement_group_id`,
`qty_delivered`, etc.) que rodam como usuários internos — uma rule `state == 'draft'`
bloquearia esses writes indevidamente. O gate Python só dispara quando o user
efetivamente é um rep.

---

## 2. Quem é o representante externo

Antes de configurar o acesso, o representante precisa existir como entidade comercial:

1. **`res.partner` com `agent = True`** — o parceiro do agente.
2. **`res.partner.sales_profile_id`** preenchido com um perfil tipo agente
   (`tr.sales.profile` com `profile_type = 'agent'`) do `tr_commercial_policy`.
3. **`res.partner.commission_id`** preenchido com uma comissão fixa ou template (do
   módulo `commission`). Obrigatório para que linhas de pedido gerem
   `sale.order.line.agent` corretamente (o `_resolve_agent_commissions` falha sem isso).

Só depois desse cadastro comercial é que o rep pode ganhar login no sistema.

---

## 3. Configuração inicial

### Instalar o módulo

```
tr_sales_rep_access
```

Depende de: `tr_commercial_policy`, `partner_stage`, `base_tier_validation`,
`partner_tier_validation`, `sale_tier_validation`, `sale`, `account`, `commission`.

### Criar o usuário de acesso do rep

Manual no backend, na **Configurações → Usuários e Empresas → Usuários**:

1. **Nome**: o nome do representante.
2. **Login**: email ou identificador.
3. **Partner**: vincular ao `res.partner` do agente (criado com `agent = True`).
4. **Grupos**: marcar apenas `Sales Rep External` (categoria **Sales Rep Access**).
   **Nenhum outro grupo de Sales/Account/Inventário/Configurações**.
5. Salvar e enviar convite de acesso.

### Vincular clientes ao rep

No cadastro de cada cliente (`res.partner`), na aba de comissões, preencher `agent_ids`
com o `res.partner` do rep. O `tr_commercial_policy` garante que apenas um agente por
cliente pode ser atribuído.

> **Nota:** apenas clientes com o rep em `agent_ids` ficam visíveis para ele. Clientes
> sem agente ou com outro agente não aparecem na lista dele.

---

## 4. O que o rep enxerga (PR 1)

Após login, o rep vê exclusivamente:

### Clientes (`res.partner`)

- Todos os parceiros cujo `commercial_partner_id.agent_ids` contém o partner dele (cobre
  matriz, contatos-filhos, endereços de entrega).
- O próprio partner do rep (exceção explícita).

### Pedidos de venda (`sale.order`)

- Pedidos cujo `sales_rep_partner_id` é ele, em **qualquer estado** (rascunho,
  confirmado, faturado).
- Pedidos criados **antes da instalação do módulo** têm snapshot `NULL` e **não**
  aparecem. Aceitável em ambiente devel-only.

### Linhas de pedido (`sale.order.line`)

- Linhas de pedidos que o rep enxerga (via snapshot do pedido).

### Faturas (`account.move`)

- Faturas cujo `sales_rep_partner_id` foi propagado do pedido de origem pelo
  `_prepare_invoice`.

### Linhas de fatura (`account.move.line`)

- Linhas de faturas no escopo dele.

### Menus visíveis

Apenas menus que `base.group_user` libera. Menus de Contabilidade, Estoque, Análise de
Vendas, Configurações e outros administrativos ficam invisíveis **por natureza aditiva
da ACL do Odoo** (não é necessário `groups="!..."`). Este comportamento é validado por
teste.

---

## 5. O que o rep NÃO enxerga (PR 1)

- Pedidos / faturas / linhas cujo `sales_rep_partner_id` é de outro agente.
- Pedidos / faturas sem snapshot (`NULL`).
- Comissões e extratos de outros agentes (proteção via record rule + ACLs — ver PR 7
  para `eng_partner_sales_info` e afins).
- Menus de Contabilidade, Estoque, Produtos (só a tela via dropdown do pedido, nos
  limites da PR do catálogo), Análise de Vendas, Configurações.
- Dados fiscais / crédito / custos / margens (à medida que forem protegidos nas PRs
  seguintes via `groups` nos campos das views).

---

## 6. Fluxo de lançamento de pedido (PR 1)

1. Rep acessa **Vendas → Cotações** e clica em "Novo".
2. Seleciona o cliente — só aparecem os clientes da carteira.
3. Ao salvar, o módulo grava `sales_rep_partner_id` = partner do rep (derivado
   automaticamente de `partner.agent_ids`).
4. Preenche linhas, confirma.
5. Ao confirmar, o pedido deixa de ser editável pelo rep (o override Python
   `_sales_rep_check_rep_can_edit` em `sale.order`/`sale.order.line` levanta
   `AccessError` quando o user é rep e o order não está em `draft`).

### Troca de cliente em rascunho

Em rascunho, o rep pode trocar o cliente do pedido. O `sales_rep_partner_id` é
**re-sincronizado** com o agente do novo cliente. Se o novo cliente não tem agente, o
snapshot fica `NULL` — o rep perde visibilidade imediata do pedido pela record rule
(esse caso é raro e intencional: trocar pra um cliente fora da carteira é um erro).

### Após confirmar

Qualquer tentativa do rep de:

- **Trocar o cliente** do pedido via RPC/import → `UserError`
- **Alterar `sales_rep_partner_id`** → `UserError`
- **Fazer `write`/`unlink`** → `AccessError` (rule de grupo)

---

## 7. Bloqueios server-side (PR 1)

### Exportação de dados

Bloqueada em duas camadas:

1. Rep não é membro de `base.group_allow_export` — o core já bloqueia `export_data()`.
2. Override de `BaseModel.export_data` levantando `AccessError` explícito para users do
   grupo rep. Defesa em profundidade para qualquer bypass futuro.

### Mudança direta de `price_unit`

Bloqueada no `write()` de `sale.order.line` pelo `tr_commercial_policy`. Não faz parte
desta PR (trava real está no módulo base de política comercial), mas é pré-requisito pra
que o rep não consiga mudar preço via API.

Campos sensíveis como `price_unit`, `discount`, `extra_discount` são governados pelo
`tr_commercial_policy`:

- **`price_unit`**: nunca editável diretamente.
- **`discount`**: via `_compute` dos canais.
- **`extra_discount`**: via wizard próprio, sujeito a tier de aprovação.

---

## 8. Troca de agente e histórico

Cenário: cliente C1 estava com agente A1, passa a ser do agente A2.

### O que acontece com pedidos antigos

- Pedidos criados enquanto o cliente era do A1 mantêm `sales_rep_partner_id = A1`.
  **Snapshot imutável após draft.**
- A1 continua enxergando esses pedidos e suas faturas.
- A2 não enxerga essa história — ele só verá pedidos criados após a troca.

### O que acontece com pedidos em rascunho

- Pedidos em rascunho que ainda estão com `A1` como snapshot: se o cliente for alterado
  para A2 por alguém interno, o snapshot é atualizado em draft (por
  `_sales_rep_prepare_- write_vals`).

### Comissão

As comissões já resolvidas em linhas de pedidos antigos continuam apontando para A1 (via
`agent_ids` da linha e snapshot do pedido). Nada é reescrito automaticamente.

---

## 9. Catálogo por agente (PR 2)

Cada agente tem duas listas de categorias de produto, editáveis apenas por **Sales
Manager**:

- `allowed_category_ids`: categorias que o rep pode vender. A inclusão é por subárvore —
  marcar uma categoria também libera todas as subcategorias.
- `excluded_category_ids`: exclusões específicas dentro da subárvore permitida. Útil
  para "tudo de Química Industrial, exceto Solventes Especiais".

A visibilidade efetiva é `descendants(allowed) − descendants(excluded)`, calculada
**on-the-fly** (sem cache) quando necessário.

### Onde configurar

**Configurações → Vendas → Sales Rep Access**: define o **default de catálogo** aplicado
a todo agente novo criado sem catálogo explícito. Alteração retroativa não acontece —
agentes existentes mantêm o catálogo atual.

**Cadastro do parceiro** (aba _Sales Rep Catalog_, visível apenas para Sales Manager e
só quando o parceiro é agente): um Sales Manager configura `allowed_category_ids` e
`excluded_category_ids` caso a caso.

### Proteção server-side

Os dois M2M são declarados com `groups="tr_commercial_policy.group_sales_manager"` no
**field definition**. Rep externo, ainda que tente via RPC
(`partner.write({'allowed_category_ids': [...]})`), recebe `AccessError`. Não é só
ocultação de view.

### Como o filtro aplica

- **Qualquer `search()`** em `product.product` e `product.template` é filtrado quando o
  usuário logado é rep: dropdowns, listas, kanban, "Ver mais" e `name_search`.
- **Constraint** em `sale.order.line`: se uma linha vier com produto fora do catálogo
  (por RPC, import, ou `message_update`), dispara `ValidationError` ao criar/alterar.
- **Fail-safe**: rep sem `allowed_category_ids` **não vê produto nenhum** — evita
  "esqueci de configurar → rep vê tudo".

### Campos sensíveis do produto

Ficam **para PR 9** (estoque, custo, margem, `standard_price`). Esta PR só filtra
_quais_ produtos o rep vê; _o que_ ele vê dentro do formulário do produto fica como
está.

---

## 10. Roadmap

Features planejadas para próximas PRs (ver `conversas_bots/plano_sales_rep_access.md`):

| PR  | Conteúdo                                                        |
| --- | --------------------------------------------------------------- |
| 2   | ✅ Catálogo por agente (ver seção 9)                            |
| 3   | Workflow Draft → Active de cliente novo (partner_stage + tier)  |
| 4   | `tr.partner.change.request` (edição cadastral por solicitação)  |
| 5   | Views do cliente para o rep (campos sensíveis com `groups`)     |
| 6   | Snapshot + tiers + `tr_rep_notes` + override de print no pedido |
| 7   | Bloqueios server-side de `eng_partner_sales_info`,              |
|     | `sale_order_line_price_history`, `sale_last_price_info`,        |
|     | `tr_pricelist_report`                                           |
| 8   | Chatter restrito (RPC test + rules + override de fallback)      |
| 9   | Estoque totalmente invisível                                    |
| 10  | Tradução pt_BR + docs finais                                    |

Cada PR incrementa este guia na seção correspondente.

---

## 11. Solução de problemas

### "Não vejo nenhum cliente"

- Verifique se o usuário está no grupo `Sales Rep External`.
- Verifique se o `res.partner` do usuário tem `agent = True`.
- Verifique se o(s) cliente(s) têm o partner do rep em `agent_ids` e se
  `sales_profile_id` está configurado no rep.

### "Não vejo um pedido que era meu"

- Pedidos criados antes da instalação do módulo têm snapshot `NULL` e não aparecem.
  Aceitável em devel-only.
- Pedidos cujo `sales_rep_partner_id` é outro agente não aparecem.

### "Erro ao confirmar pedido: linha de comissão sem `commission_id`"

Falta `commission_id` no cadastro do `res.partner` do agente. Preencha com uma comissão
de `commission_type = 'fixed'` ou template via `tr_commercial_policy`.

### "Export retorna AccessError"

Comportamento esperado: reps não podem exportar. Peça ao time interno para exportar, se
necessário.

### "Write no pedido retorna AccessError"

Se o pedido já foi confirmado, é esperado — rep só edita em rascunho. Se é um rascunho e
o erro aparece:

- Verifique se o pedido realmente tem `sales_rep_partner_id = partner do rep`.
- Verifique se o `state` é realmente `draft`.
- Verifique se alguma rule de grupo removeu permissão do rep (o grupo deve implicar
  apenas `base.group_user`).

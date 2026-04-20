# Guia de Uso — Sales Rep Access

Módulo que dá acesso controlado ao backend Odoo para representantes comerciais externos.
Este guia é vivo: cresce a cada PR entregue do roadmap em
`conversas_bots/plano_sales_rep_access.md`.

Status atual: **PR 7 — Bloqueios server-side em módulos companion (sales analysis, price
history, pricelist report)** (+ PR 1, 2, 3, 4a, 4b, 5, 5b, 6a e 6b mergeadas).

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
10. [Workflow Draft → Active de cliente novo (PR 3)](#10-workflow-draft--active-de-cliente-novo-pr-3)
11. [Solicitações de alteração cadastral (PR 4a)](#11-solicitações-de-alteração-cadastral-pr-4a)
12. [Observações do rep no pedido (PR 6a)](#12-observações-do-rep-no-pedido-pr-6a)
13. [Conferência do pedido e impressão (PR 6b)](#13-conferência-do-pedido-e-impressão-pr-6b)
14. [Bloqueios server-side em módulos companion (PR 7)](#14-bloqueios-server-side-em-módulos-companion-pr-7)
15. [Roadmap — o que vem nas próximas PRs](#15-roadmap)
16. [Solução de problemas](#16-solução-de-problemas)

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

## 10. Workflow Draft → Active de cliente novo (PR 3)

Quando um representante cria um cliente novo, o cadastro **nasce em Draft** e passa por
validação de um conferente (Sales Manager) antes de virar Active. Só clientes Active
podem ser usados em pedidos.

### Quando o fluxo dispara

- **Sempre que um usuário do grupo rep** criar um `res.partner` novo com `parent_id`
  vazio (ou seja, um cliente comercial raiz, não um contato filho).
- O stage do novo partner é **forçado** a Draft mesmo se o vals recebido trouxer
  `stage_id=active` — proteção contra bypass via RPC/import.
- O `agent_ids` do novo cliente também é controlado pelo módulo:
  - Se o rep não preencher, é preenchido automaticamente com o próprio rep.
  - Se o rep tentar atribuir outro representante (ou deixar em branco) via RPC/import, o
    `create` levanta `ValidationError`. Regra de negócio: cliente cadastrado por rep
    **pertence ao rep**; rep não pode atribuir cliente novo a outro representante.
- Admin ou outros usuários internos **não** são afetados. Cliente criado por eles segue
  o fluxo default do `partner_stage` (nasce Active) e pode ter qualquer `agent_ids`.

### Tier de validação

Em **Discuss → Reviews** (ou na caixa do conferente), o Sales Manager vê os novos
clientes pendentes. Ao clicar em **Validate**, a revisão é aprovada. Só depois disso o
conferente (ou qualquer manager) pode promover `stage_id = Active` pelo cadastro do
partner — o `base_tier_validation` bloqueia a transição enquanto houver revisões
pendentes.

### O que acontece com contatos filhos

Contatos filhos (`parent_id` preenchido) **não** são forçados a Draft e **não** disparam
tier. Eles nascem no stage default (Active). A proteção contra vender ao cliente
não-aprovado vem do `sale.order`, que checa `partner_id.commercial_partner_id.state` —
um filho Active de um commercial Draft continua bloqueado.

### Guardas no pedido

Tentar criar (ou redirecionar) pedido de venda para cliente cujo commercial está em
Draft dispara `ValidationError`. O mesmo check roda também em `action_confirm()`,
cobrindo o caso raro em que o cliente foi rebaixado a Draft depois de o pedido ter sido
criado.

### Clientes que já existiam

Clientes criados antes da instalação deste módulo permanecem no stage que tinham
(Active). Não há migração retroativa. Rep que precisar ver esses clientes encontra os da
sua carteira normalmente — o workflow só se aplica a **novos** cadastros feitos pelo
rep.

---

## 11. Solicitações de alteração cadastral (PR 4a)

Depois que um cliente está Active, o rep **não edita** campos cadastrais direto: abre
uma **solicitação de alteração** (`tr.partner.change.request`) e um Sales Manager aprova
antes de a mudança ser aplicada.

### Quando usar

- Mudança de telefone, e-mail, endereço, estado, país, CEP.
- Criação de novo contato-filho (comprador, financeiro, etc) depois que o cliente já
  está Active.

### O que NÃO passa pelo change_request

- Condição comercial (`sales_profile_id`, `commission_id`, banda de desconto, pricelist,
  payment_term): Sales Manager ajusta direto no cadastro, usando o fluxo próprio do
  `tr_commercial_policy`.
- Criação de cliente novo: continua pelo form padrão (fluxo da PR 3, força Draft +
  tier).
- Criação de child_ids antes de o cliente ser Active: rep faz direto no form do partner.

### Fluxo do rep

1. Abre o cliente no **Vendas → Clientes**.
2. Clica em um dos botões no topo:
   - **Solicitar alteração** → formulário pra adicionar linhas com campo + novo valor.
     Whitelist: phone, mobile, email, street, street2, city, zip, state_id, country_id.
   - **Solicitar novo contato** → campos do contato novo (nome, e-mail, telefone,
     função, tipo).
3. Preenche o motivo (`reason`) e salva. O request nasce em **Pendente** e o Sales
   Manager recebe notificação pelo `tier.validation`.
4. Enquanto a solicitação está Pendente, o rep pode **Cancelar** pra abrir outra
   (unicidade: só 1 pendente por cliente).

### Fluxo do Sales Manager

1. **Discuss → Reviews** (ou **Vendas → Change Requests**) mostra as solicitações
   pendentes.
2. Analisa o motivo + payload.
3. Clica em **Aprovar** (aplica direto no cliente ou cria o filho, dependendo do tipo)
   ou **Rejeitar** (marca como `rejected`, nada muda no partner).
4. Pode **deletar** (menu contextual) solicitações rejeitadas ou canceladas — pending e
   approved são protegidas (audit trail).

### Guardas server-side (PR 4a)

- Unicidade: um único `pending` por partner. `sudo().search_count` para não deixar rep
  abrir um segundo pendente invisível (por record rule) ao primeiro.
- Whitelist tipada: `field_id` restrito via domain lambda + `@api.constrains`. Payload
  (char / reference) validado contra `field_id.ttype` e `field_id.relation`.
- Authorship: `requested_by` e `sales_rep_partner_id` são forçados pelo create quando o
  user é rep, ignorando o que vier no vals (anti-forge).
- Payload pós-Pending: linhas e campos de payload são read-only via override de
  `create/write/unlink` na linha + `IMMUTABLE_FIELDS_AFTER_PENDING` no header.
- State machine: `pending → approved / rejected / cancelled`. Qualquer write direto de
  `state` via RPC é bloqueado — só os botões `action_*` podem transicionar.
- Tier: reviewer é `tr_commercial_policy.group_sales_manager`. `has_comment=True` não é
  suportado — se alguém ativar via RPC, `action_approve` / `action_reject` levantam
  `UserError` em vez de ignorar o wizard de comentário.
- Reviews pendentes: canceladas ou rejeitadas são explicitamente fechadas pra não deixar
  reviews penduradas com mais de uma tier.definition.

### Bloqueio de write direto (PR 4b)

A PR 4b fechou o ciclo: rep **não faz `write` direto** em `res.partner` quando o cliente
está Active — qualquer mudança cadastral passa obrigatoriamente por um change_request.

- Mensagens no chatter e atividades agendadas continuam livres (não são business data).
- Editar partner em Draft (pré-aprovação da PR 3) segue funcionando normalmente.
- Os três botões da condição comercial do `tr_commercial_policy`
  (`action_create_commercial_condition`, `action_create_override_condition`,
  `action_remove_override_condition`) usam `.sudo()` internamente pra escrever
  `commercial_condition_id` no partner — o rep clica no botão normalmente, o módulo
  garante os invariantes e o guard da 4b deixa passar.
- **Limitação intencional**: edição de contato-filho existente em partner Active também
  cai no guard. Hoje não há fluxo pra isso (rep só cria filho novo via `new_child`). Se
  virar necessidade, uma PR futura estende o change_request com `field_update_child`.

O **wizard de propagação** do plano original (partner → pedidos em aberto) foi
**descartado** nesta iteração: a whitelist atual é puramente cadastral e nenhum campo é
copiado pro `sale.order` / `account.move` (o pedido só tem M2O pro partner; endereço
flui via related dinâmico). Se a whitelist crescer pra incluir snapshot fields (ex:
`property_payment_term_id`), PR 4c dedicada.

---

## 12. Observações do rep no pedido (PR 6a)

Campo livre de texto (`tr_rep_notes`) no pedido, pra rep e manager coordenarem via notas
operacionais durante a negociação.

**Onde aparece:** aba "Other Info" do formulário de pedido → grupo "Sales Rep" (logo
após "Invoicing and Payments").

**Quem edita:**

- Rep: só enquanto o pedido está em **draft** (cotação). O guard
  `_sales_rep_check_rep_can_edit` do PR 1 bloqueia qualquer write do rep depois que o
  pedido confirma — incluindo esse campo. Sem exceção de whitelist.
- Manager / admin / diretor: edita em qualquer state (draft, sale, done, cancel).

**O que ele faz:**

- Campo texto simples, sem limite de tamanho.
- **Não** é rastreado no chatter (`tracking=False`). Edições não geram histórico
  automático — é um rascunho operacional, não trilha de auditoria. Se precisar de
  histórico depois, abriremos PR dedicada.
- **Não** é impresso no PDF de cotação/pedido. Rep anota sem preocupação de texto
  contratual.

**Cenários típicos:**

- "Cliente pediu desconto por volume, aprovação já pedida ao Diretor."
- "Combinada entrega pra 3ª feira, confirmar saída da NFe até 2ª."
- "Cliente sinalizou que pode comprar mais 2 pallets se vier amostra."

Quando o pedido confirma, a responsabilidade de atualizar notas passa pro manager — rep
informa a novidade por chat/telefone e manager registra. Isso mantém coerência com a
regra geral do PR 1 de que o rep não edita nada pós-confirm.

---

## 13. Conferência do pedido e impressão (PR 6b)

Todo pedido lançado pelo representante externo passa por uma **conferência operacional**
antes de ser confirmado. Conferente valida estoque, configuração fiscal e prazo de
entrega — **não** valida desconto comercial (isso é papel do Gerente/Diretor de Vendas
via `tr_commercial_policy`).

### Quem conferencia

Usuário com o grupo **Conferente de Pedidos de Representante**
(`tr_sales_rep_access.group_sales_rep_checker`). Ao criar o usuário, adicione também um
grupo de acesso a `sale.order` (tipicamente `sales_team.group_sale_salesman`) — o grupo
do conferente é **papel funcional**, não libera menu de Pedidos por si só.

### Como funciona

Quando o representante cria um pedido:

1. Campo técnico `tr_rep_conference_required` fica `True` (setado automaticamente pelo
   `create`, nunca preenchido manualmente).
2. O sistema dispara automaticamente um **review pendente** do tier "Conferência de
   pedido do representante", endereçado ao grupo Conferente.
3. Se o pedido também envolver desconto além do limite do vendedor (desconto comercial
   do `tr_commercial_policy`), tier Gerente/Diretor entra em paralelo.
4. Conferente recebe e-mail de alerta (`notify_on_create` do tier_definition) e aprova
   com 1 clique (`has_comment=False` — sem comentário obrigatório).

Pedidos criados por admin/gerente diretamente (ex: migração, importação) **não** ativam
a conferência. O fluxo é pensado pro rep externo.

### Impressão da cotação antes do confirm

Comportamento padrão do `sale_tier_validation` (quando o flag `sale_report_print_block`
está ligado na empresa) bloqueia impressão de qualquer pedido que tenha review pendente.
Esse módulo relaxa o bloqueio para o caso específico da conferência:

- **Só Conferente pendente** → rep imprime a cotação. Essa tier valida operacional; não
  vincula a Trento a preço/condição.
- **Gerente ou Diretor pendente** → bloqueia. Imprimir uma cotação com desconto não
  aprovado vincularia a empresa a um valor que ainda não foi autorizado.
- **Nenhum tier pendente (`validation_status == 'validated'`)** → imprime normal.
- **Pedido sem `tr_rep_conference_required`** (ex: pedido admin) → comportamento normal
  do core, sem override.

### Troubleshooting

**Cenário**: Conferente aprovou, pedido ainda aparece como "não validado".

Verifique se há tier Gerente/Diretor pendente. Rep pode ter gerado desconto acima do
limite do perfil; o tier comercial roda em paralelo e precisa ser aprovado separadamente
antes do pedido virar `validated`.

**Cenário**: Admin/gerente quer forçar um pedido pela conferência.

Setar manualmente `tr_rep_conference_required=True` no pedido e chamar
`request_validation()`. Não há UI pra isso hoje (raro o suficiente pra não ter botão
dedicado).

---

## 14. Bloqueios server-side em módulos companion (PR 7)

Três classes de exposição que o `tr_sales_rep_access` fechou sobre módulos companheiros
já instalados no stack. Cada uma bloqueia um tipo de vazamento diferente, todas
server-side — **não confiar em esconder via view** é o princípio (DA-6 do plano).

### Estatísticas agregadas do cliente (`eng_partner_sales_info`)

O módulo companheiro expõe 21 campos de análise comercial em `res.partner`
(`last_order_id`, `order_count`, `total_ordered`, `average_ordered`,
`days_since_last_order`, etc.). Upstream já restringe a **aba de análise** no form ao
grupo `eng_partner_sales_info.group_partner_sales_analysis`, mas isso é hide view-only —
leitura via RPC, `fields_get` ou `search` continua aberta a qualquer usuário interno,
inclusive ao rep.

O `tr_sales_rep_access` adiciona um bloqueio server-side **específico para o rep**: cada
field é redeclarado com `groups="!tr_sales_rep_access.group_sales_rep_external"`,
excluindo o rep da leitura em todos os caminhos do ORM. Usuários internos fora do
analysis group continuam podendo ler via RPC (comportamento upstream preservado); só o
rep é rejeitado. Auditoria regressiva da membership:
`test_rep_is_not_in_partner_sales_analysis_group`.

### Histórico de preços (`sale_order_line_price_history`)

O módulo companion planta dois widgets interativos no sistema:

- Widget "Show price history" no tree/kanban da linha do pedido.
- Widget "Set price from history" dentro do wizard de histórico.

Ambos ficam escondidos do rep via `groups=!rep` no field com widget. A barreira primária
do wizard hoje é o ACL do upstream (rep não é membro do grupo que tem read na model
`sale.order.line.price.history`), mas o hide na view serve de defense in depth caso o
ACL mude no futuro.

### Relatório de lista de preços do parceiro (`tr_pricelist_report`)

O "Print Price List" do partner gera um relatório com toda a tabela de preços do cliente
— inclusive produtos fora do catálogo permitido do rep (ver PR 2). Bloqueio em duas
camadas:

- **Barreira primária (server-side)**: override em `res.partner.action_print_pricelist`
  levanta `AccessError` se o caller é rep. Cobre tanto o clique no botão do form quanto
  o entrypoint do Action menu (que o upstream expõe via `ir.actions.server` com
  `binding_model_id = res.partner` em todo list/form de partner).
- **Limpeza de UI (defense in depth)**: o botão "Print Price List" no form do partner
  some via `groups=!rep`. O item do Action menu **continua visível** na lista/form de
  partner — é o guard server-side que realmente segura o acesso.

### Decisões conscientes não cobertas pela PR 7

- **`server_action_mass_edit`**: wizard ACL'd para `base.group_user` (rep herda
  transitivamente). Hoje não existe `ir.actions.server` configurada como mass-edit no
  stack, então o rep não tem entrypoint UI. Os write-guards das PRs 1/4b/6b limitam o
  dano se alguém adicionar uma mass-edit action depois. Revisar quando surgir uma
  mass-edit real.
- **`groups_restrict_price_change`**: não está instalado no stack.
- **Relatórios Trento** (`trento_report_invoice`, `trento_report_sale`,
  `trento_report_utils`): cobertos pelo grupo contábil que o rep nunca integra, pela
  record rule de sale.order, ou são puro boilerplate.

---

## 15. Roadmap

Features planejadas para próximas PRs (ver `conversas_bots/plano_sales_rep_access.md`):

| PR  | Conteúdo                                                             |
| --- | -------------------------------------------------------------------- |
| 2   | ✅ Catálogo por agente (ver seção 9)                                 |
| 3   | ✅ Workflow Draft → Active de cliente novo (ver seção 10)            |
| 4a  | ✅ `tr.partner.change.request` + tier + view (ver seção 11)          |
| 4b  | ✅ Bloqueio de write direto em `res.partner` Active (ver seção 11)   |
| 5   | ✅ Hide server-side dos campos não-operacionais sensíveis do partner |
| 5b  | ✅ Readonly-active de contact fields no form do partner              |
| 6a  | ✅ `tr_rep_notes` no pedido (ver seção 12)                           |
| 6b  | ✅ Tier Conferente + override de print block (ver seção 13)          |
| 7   | ✅ Bloqueios server-side de `eng_partner_sales_info`,                |
|     | `sale_order_line_price_history`, `tr_pricelist_report` (ver          |
|     | seção 14). `sale_last_price_info` fica coberto pela record           |
|     | rule do PR 1 — sem código adicional.                                 |
| 8   | Chatter restrito (RPC test + rules + override de fallback)           |
| 9   | Estoque totalmente invisível                                         |
| 10  | Tradução pt_BR + docs finais                                         |

Cada PR incrementa este guia na seção correspondente.

---

## 16. Solução de problemas

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

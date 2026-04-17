# tr_commercial_policy

Política comercial centralizada: condições comerciais, perfis de vendas, descontos
estruturados e comissões dinâmicas.

## Arquitetura

### Modelos Principais

- **`tr.sales.profile`** — perfil de vendas (agent ou internal). Define limites de
  desconto, regras por produto e bandas de comissão/valor.
- **`partner.commercial.condition`** — condição comercial do parceiro. Um por (partner,
  company). Contém pricelist, descontos, retorno contratual, condições de pagamento,
  modo de pagamento, incoterm e método de entrega.
- **`partner.commercial.condition.line`** — desconto por produto/template na condição.
- **`tr.discount.approval.line`** — linha de aprovação de desconto extra.

### Herança por Grupo

A condição comercial pertence ao parceiro ou ao grupo (`company_group_id`):

- `effective_condition_id` (compute no `res.partner`): própria → grupo → vazio
- `condition_inherited` / `condition_is_override`: indicadores visuais
- Na view do parceiro: banners azul (herança) / amarelo (override) + botões
- Constraint: condição deve pertencer ao próprio parceiro ou ao seu grupo

### Sync para Property Fields

Quando a condição muda, sincroniza para os property fields do parceiro via ORM write
(mantém `company_dependent` para multi-company e queries SQL):

| Campo na condição     | Campo no parceiro              |
| --------------------- | ------------------------------ |
| `pricelist_id`        | `property_product_pricelist`   |
| `payment_term_id`     | `property_payment_term_id`     |
| `payment_mode_id`     | `customer_payment_mode_id`     |
| `incoterm_id`         | `sale_incoterm_id`             |
| `delivery_carrier_id` | `property_delivery_carrier_id` |
| `contractual_return`  | `punctuality_discount`         |

Triggers: `_sync_to_partners()` no write/create da condição, e
`_sync_partner_fields_from_condition()` no write do parceiro.

## Fluxo de Precificação

```
base_price (pricelist)
    ↓
reference_price = base_price × (1 + adjustment_factor%)
    ↓ (adjustment_factor vem do contractual_return + tax_rate + freight_rate + admin_rate)
price_unit = reference_price × (1 - (seller_discount + extra_discount)%)
    ↓
discount_value = qty × price_unit × (cash_discount + fob_discount)%
```

### Funções centralizadas (policy_utils.py)

Toda lógica de cálculo de preço está centralizada em `policy_utils.py` para garantir que
computes, onchanges, wizards e qualquer outro ponto usem a mesma fórmula:

- `calc_adjustment_factor(cr_pct, tax_rate, freight_rate, admin_rate)` → fator de ajuste
  %
- `calc_reference_price(base_price, cr_pct, tax_rate, freight_rate, admin_rate)` → preço
  de referência
- `calc_price_unit(reference_price, seller_discount, extra_discount)` → preço unitário
- `get_policy_rates(env)` → `(tax_rate, freight_rate, admin_rate)` em decimal, lidos das
  chaves `*_pct` do `ir.config_parameter`

```python
cr = contractual_return / 100
total_rate = tax_rate + freight_rate + admin_rate   # composição aditiva
numerator = 1 - total_rate                           # default: 1 - 0.10 - 0.05 - 0.02
denominator = 1 - cr - total_rate
adjustment_factor = (numerator / denominator - 1) × 100
```

### Parâmetros do sistema (`ir.config_parameter`)

Configurados via `Configuração → Vendas → Commercial Policy`. Valores armazenados em
**percentual** (ex.: `10.0`, não `0.10`); `get_policy_rates` divide por 100 antes de
devolver.

- `tr_commercial_policy.tax_rate_pct` — imposto composto (default `0.0`, configurar por empresa)
- `tr_commercial_policy.freight_rate_pct` — frete (default `0.0`, configurar por empresa)
- `tr_commercial_policy.admin_rate_pct` — despesa administrativa (default `0.0`, configurar por empresa)

As chaves antigas sem sufixo (`tax_rate`, `freight_rate`) **foram descontinuadas** —
módulo não as lê mais. Se sobrar valor nelas no banco, é órfão inofensivo.

### Resolução de Desconto por Produto

Hierarquia (do mais específico ao geral):

1. Variante do produto (`product_id`)
2. Template do produto (`product_tmpl_id`)
3. Categoria (sobe na árvore de categorias)
4. Geral (`applied_on='general'`)

## Perfis de Vendas

### Agent (Representante)

- Usa **bandas de comissão** (`commission_band_ids`)
- Exemplo: desconto até 5% → comissão 10%, até 10% → 7%
- Constraint: taxas de comissão devem ser estritamente decrescentes
- `commission_rate` no pedido é dinâmico, resolve da banda

### Internal (Equipe Interna)

- Usa **bandas de valor do pedido** (`order_value_band_ids`)
- Exemplo: pedido R$1000+ → max 5%, R$5000+ → max 10%
- Constraint: descontos devem ser estritamente crescentes
- Usa média de 6 meses do parceiro para determinar banda aplicável

### Resolução do Perfil no Pedido

1. Agente do cliente → perfil do agente
2. Usuário vendedor → perfil do usuário
3. Equipe de vendas → perfil da equipe

## Fluxo de Aprovação (Desconto Extra)

1. Vendedor seta `extra_discount` + `extra_discount_reason` na linha
2. `action_request_discount_approval()` cria `tr.discount.approval.line`
3. Nível requerido: `extra_discount > manager_extra_limit` → diretor, senão → gerente
4. Manager/Diretor aprova ou rejeita
5. `action_confirm()` valida que todas as aprovações estão completas

## Validações no action_confirm

1. `_check_sales_profile_required()` — pedido precisa de perfil
2. `_check_commercial_condition_required()` — parceiro precisa de condição
3. `_check_pricelist_matches_profile()` — pricelist do pedido deve estar nas
   `pricelist_ids` do perfil (se vazio, aceita qualquer uma)
4. `_check_orphan_commissions()` — sem comissões órfãs
5. `_validate_seller_discount_limit()` — desconto dentro do max (com bypass aprovado)
6. `_check_extra_discount_approval()` — aprovações completas
7. `_check_internal_band_approval()` — banda interna validada

## Proteção de Preço

`_check_direct_price_edit()` bloqueia writes diretos em `price_unit`/`discount`.
Exceções: computes (context `tr_skip_price_protection`) e admin.

## Integração com Fatura/Boleto

- `_prepare_invoice()` copia: cash_discount, fob_discount, contractual_return
- `_prepare_invoice_line()` copia: commission_rate
- `punctuality_discount` no pedido → `invoice_punctuality_discount` na fatura →
  `boleto_discount_perc` na linha (via `eng_punctuality_discount`)

### Relação com `eng_punctuality_discount`

O módulo `eng_punctuality_discount` (engenere-addons) é **infraestrutura genérica** que
transporta um percentual de desconto do parceiro até o boleto. Ele define:

- `res.partner.punctuality_discount` → `sale.order.punctuality_discount` →
  `account.move.invoice_punctuality_discount` → `account.move.line.boleto_discount_perc`
  (consumido pelo l10n-brazil pro boleto)

O `tr_commercial_policy` é a **fonte do dado**: o `contractual_return` da condição
comercial é sincronizado para `res.partner.punctuality_discount` via
`_sync_to_partners()`, e a partir daí o `eng_punctuality_discount` cuida da propagação
até o boleto.

**Decisão:** manter o `eng_punctuality_discount` intacto e genérico (serve outros
clientes Engenere). Não absorver, não adaptar, não renomear. Na view do pedido, o campo
`punctuality_discount` fica invisível — a UI mostra apenas `contractual_return` com
fator de ajuste. O módulo legado funciona como camada de transporte transparente.

## Decisões de Arquitetura

### pricelist_ids no perfil (M2M)

O campo `pricelist_ids` no perfil é M2M (permite múltiplas pricelists). No cenário
estável futuro, cada perfil terá apenas 1 pricelist (tabela base do tier). O M2M serve
principalmente durante a transição do modelo atual (múltiplas tabelas legadas) para o
modelo novo (tabela única + descontos). Quando estabilizar, avaliar migração para M2O.

### Módulo de transição (futuro)

Para a migração do sistema atual (tabelas com preço final) para a política comercial
(tabela base + descontos), criar módulo separado `trento_commercial_policy_transition`:

- Flag na pricelist para desabilitar fator de ajuste do retorno contratual
- Restrição de pricelist por parceiro (esta tabela só pode ser usada por estes clientes)
- Flag na pricelist para bloquear descontos (seller/extra travados em 0)

Desinstalar quando a transição estiver concluída.

## Hook de Instalação (hooks.py)

Na primeira instalação, cria condições para parceiros com vendas confirmadas:

1. Cria perfis padrão (agent + internal) se não existem
2. Atribui perfil a agentes sem perfil
3. Para grupos: cria condição no cabeça, membros herdam via `ir.property`
4. Para individuais: cria condição com valores do parceiro
5. Usa SQL direto para evitar cascatas de recompute do ORM

## Cron

`_cron_cleanup_orphan_conditions()` — remove diariamente condições não referenciadas por
nenhum parceiro ou pedido.

## Dependências

`sale_management`, `l10n_br_sale`, `base_partner_company_group`,
`sale_partner_company_group`, `engenere_commission`, `account_payment_mode`,
`account_payment_sale`, `delivery`, `sale_stock`, `sale_partner_incoterm`,
`eng_punctuality_discount`, `trento_sale_usability`

## Política Comercial na Fatura

### Decision Tree (`action_post`)

O `action_post` valida apenas `out_invoice`. Refunds (`out_refund`) são livres.

```
1. Manual invoice (sem sale_origin):
   → prerequisites (partner, condition)
   → own rules (validate_seller_discount_limit)
   → tier check (need_validation)

2. Sale-origin invoice:
   a. Mixed lines (sale + manual)? → hard block
   b. Heterogeneous origins? → hard block
   c. Orders confirmed? → if not, own rules + tier
   d. Within validity? → if not, own rules + tier
   e. Snapshot checks:
      - Classe B/C divergence → hard block
      - Classe A divergence or qty excess → own rules + tier
   f. Parity → post free
```

### Classificação de Divergências

- **Classe A (own rules):** seller_discount, extra_discount, extra_discount_reason,
  cash_discount, fob_discount, payment_term
- **Classe B/C (hard block):** base_price, reference_price, price_unit,
  contractual_return, commission_rate, commission, agents

### Validade Temporal

Campo `invoice_validity_days` em `res.company` (via settings). Se a invoice é mais velha
que o prazo (baseado no `date_order` mais antigo), bypassa snapshot e entra em own
rules.

### Proteção de Preço por Categoria

Campo `allow_manual_price_edit` em `product.category` (default False, grupos director).
Write-time guard em `account.move.line.write()` bloqueia edição direta de `price_unit` e
`discount` em `out_invoice`. Exceção por categoria só para linhas manuais (sem sale
origin). Admin e context flag (`tr_skip_price_protection`) também bypassam.

Post-time fallback (`_check_manual_price_integrity`) respeita a mesma exceção por
categoria. `discount` protegido só no write (não no post) para evitar conflito com
módulos que recalculam (`l10n_br`, `engenere_account_invoice_br_discount`).

Override de `import_fiscal_document` adiciona `tr_skip_price_protection` no
contexto.

### Refund (`out_refund`)

Campos da política presentes para rastreabilidade, mas sem proteção de edição e sem
validação no `action_post`. Reversal copia todos os campos de política (header +
linhas + agents). Commission amounts são negativos na reversal.

### Tier Validation na Fatura

Usa `account_move_tier_validation` (OCA). Tier definitions para
`discount_approval_level` manager/director. `_get_under_validation_exceptions` permite
editar campos de desconto enquanto validação está pendente.

## Notas Técnicas

- `commercial_condition_id` no parceiro é `company_dependent` (property field)
- `commercial_condition_id` no pedido é `compute + store + readonly` — sempre vem do
  parceiro, nunca editável manualmente
- `punctuality_discount` no pedido é `compute + store` — congela após confirmação
- Campos escondidos no cadastro do parceiro: pricelist, payment term, payment mode,
  incoterm, delivery carrier, punctuality discount (gerenciados pela condição)
- `display_name` da condição: "Condição de [Parceiro]"

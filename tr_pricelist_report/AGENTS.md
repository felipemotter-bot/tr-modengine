# tr_pricelist_report

Relatório de lista de preços da empresa, gerado a partir da
`partner.commercial.condition`. Substitui os pontos nativos de impressão de pricelist
(que ignoram `contractual_return`, `adjustment_factor`, `seller_discount` e demais
inputs da política comercial).

## Arquitetura

### Fonte da verdade

O relatório **sempre** parte de uma `partner.commercial.condition`. Nunca de uma
pricelist solta. Isso porque, em produção, o preço impresso ao cliente depende de:
pricelist base + `contractual_return` + `tax_rate` + `freight_rate` + `admin_rate` +
`seller_discount` (resolvido via linhas da condição), e `pricelist_item.fixed_price`
sozinho não representa nenhum desses.

Pontos de entrada:

- Botão no formulário de `partner.commercial.condition`.
- Botão no formulário de `res.partner` quando `effective_condition_id` existir. A
  condição efetiva pode ser própria ou herdada do grupo, mas o PDF não menciona grupo —
  imprime como se fosse do parceiro.

### Reuso obrigatório

Para garantir paridade com o preço que `sale.order.line` calcula, o relatório **nunca**
reimplementa a fórmula nem lê `pricelist_item.fixed_price` diretamente. A cadeia é:

1. `sale.order._get_fresh_base_price(line_stub, condition)` →
   `product._get_tax_included_unit_price(...)` devolve `base_price` respeitando política
   fiscal e `skip_adjustment_factor` do `trento_commercial_policy_transition`.
2. `policy_utils.get_policy_rates(env)` → taxas vigentes.
3. `policy_utils.calc_reference_price(base, cr, tax, freight, admin)` →
   `reference_price`.
4. `partner.commercial.condition._resolve_discount_for_product(product)` →
   `(seller, extra, level)`.
5. `policy_utils.calc_price_unit(reference, seller, 0.0)` → `price_unit`.

`extra_discount` é **sempre zero** no relatório — não é concedido a priori, só em pedido
específico.

### Desabilitação dos entry points nativos

Para evitar que usuários imprimam a "lista nua" do core Odoo (que daria
`pricelist_item.fixed_price` sem nada da política), o módulo desabilita:

- Core: `product.action_product_price_list_report` e
  `product.action_product_template_price_list_report` (via XML de `data/`).
- Condicional (se `product_pricelist_direct_print` estiver instalado): seus 6 bindings +
  menu, via `post_init_hook`.

Estratégia: **remover visibilidade** (`binding_model_id = False`,
`binding_view_types = ''`, menu `active = False`). Não deletar — testes internos do core
que chamam o action por código continuam funcionando.

## Wizard

`tr.pricelist.report.wizard` (`TransientModel`). Campos PR1:

- `condition_id` — `partner.commercial.condition`, obrigatório.
- `layout` — selection (PR1: só `por_categoria`).
- `category_ids` — M2M `product.category`.
- `date_end` — validade do PDF; default = hoje + `validity_days`.
- `discount_display` — selection (`show_discounts` / `net_price`); default vem da
  condição, vendedor pode sobrescrever por impressão.

PRs futuras adicionam: `layout=geralzao` + `group_axis` (PR2), `historico` (PR3),
`send_by_email` (PR4).

## Consolidação template-first

Dentro de cada seção (categoria nesta PR), produtos são consolidados por
`product.template`:

- Se todas as variantes do template compartilham o mesmo **tratamento comercial**, sai 1
  linha só (descrição do template + preço), com as variantes listadas inline em fonte
  menor (`[default_code] valores dos atributos`).
- Variantes cujo tratamento comercial diverge vão pra **seção de exceções** no final do
  PDF, separadas em:
  - "Preços Especiais por Variante" — variantes com `pricelist_item` próprio.
  - "Preços por Quantidade" — `pricelist_item` com `min_quantity > 0`.

### Regra de "mesmo tratamento comercial" (decisão Felipe, 2026-04-17)

O bucket de consolidação é sempre pela tripla
`(price_unit, reference_price, seller_discount)`. Se **qualquer um dos três campos
diverge** entre duas variantes, elas não consolidam — a divergente vai pra exceção. Vale
em ambos os modos de `discount_display` (`net_price` e `show_discounts`), sem exceção.

Isso é **regra de negócio**, não decisão técnica. Em produção:

> _"Quando uma variante muda de valor — seja pelo preço seja pelo desconto — ela precisa
> ser mostrada separada, nunca junto. Quando falamos de variantes, falamos de
> exceções."_

Implicação deliberada: no modo `net_price`, duas variantes com o mesmo `price_unit` mas
caminhos distintos (ex.: base diferente compensada por desconto diferente) **aparecem em
linhas separadas**. A lista inline significa "mesmo contrato comercial", não "mesmo
valor numérico final". Não trocar por consolidação adaptativa ao `discount_display` sem
conversar com o Felipe antes.

## `ir.config_parameter`

- `tr_pricelist_report.validity_days` (default 30) — criado nesta PR.
- `tr_pricelist_report.category_depth` (default -1) — criado nesta PR.
- `tr_pricelist_report.group_attribute_name` / `group_attribute_id` — PR2.
- `tr_pricelist_report.history_months_back` — PR3.

## Invariante

Paridade com `sale.order.line._compute_price_unit`: para o mesmo `(produto, condição)`,
o `price_unit` exibido no PDF é **idêntico** ao que o pedido calcularia. Testado em
`tests/test_pricing_consistency.py`.

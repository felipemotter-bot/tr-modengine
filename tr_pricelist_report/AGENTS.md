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

`tr.pricelist.report.wizard` (`TransientModel`). Campos:

- `condition_id` — `partner.commercial.condition`, obrigatório.
- `layout` — selection `por_categoria` / `geralzao`.
- `group_axis` — selection `marca` / `categoria`. Exigido quando `layout=geralzao`.
- `category_ids` — M2M `product.category`. Obrigatório quando `layout=por_categoria`;
  opcional em `geralzao` (vazio = todo catálogo vendável).
- `date_end` — validade do PDF; default = hoje + `validity_days`.
- `discount_display` — selection (`show_discounts` / `net_price`); default vem da
  condição, vendedor pode sobrescrever por impressão.

Todas as capacidades planejadas entregues (PR1–PR5).

## Layouts

### `por_categoria`

- Filtro: vendedor seleciona uma ou mais `product.category` no wizard.
- Agrupamento: resolvedor único de categoria (ver abaixo), respeita `category_depth`.

### `geralzao`

Escopo: todos os produtos `active` + `sale_ok` da base (ou filtrado por `category_ids`
se preenchido). Dois sub-modos via `group_axis`:

- **Modo A — `marca`**: particiona pelos valores do atributo configurado em
  `tr_pricelist_report.group_attribute_name` (default `"MARCA"`). O `post_init_hook`
  resolve o id do atributo no banco e grava em `tr_pricelist_report.group_attribute_id`.
  Produtos sem o atributo (ou quando o próprio atributo não existe) caem em seções
  adicionais ao final via **fallback por categoria** — mesmo resolvedor do Modo B, para
  não criar duas noções de categoria.
- **Modo B — `categoria`**: particiona pela categoria no nível configurado por
  `category_depth`.

### `historico`

Layout "lista de recompra" pro cliente: produtos que o parceiro comprou nos últimos N
meses, agrupados pelo mesmo resolvedor de categoria do geralzão Modo B.

- Escopo: `sale.order.line` com `order_id.partner_id = condition.partner_id`,
  `order_id.state in ('sale','done')` e
  `order_id.date_order >= today - history_months_back`.
- Produto precisa estar `active=True` **e** `sale_ok=True` — arquivado ou não-vendável
  hoje sai do relatório mesmo que o cliente tenha comprado.
- **Ignora** a flag `tr_exclude_from_general_pricelist` — produto customizado que o
  cliente comprou aparece pra ele poder recomprar.
- **Variante por variante**, sem consolidação template-first — o histórico reflete
  exatamente o que o cliente comprou.
- Colunas do PDF: `Código | Descrição | [Preço Ref. | Desc. % |] Qtd. comprada | Preço`
  (as duas colunas intermediárias só aparecem em `show_discounts`).
- Coluna `Qtd. comprada` mostra valor agregado na UoM default do produto, acompanhado do
  label da UoM (ex.: `"120 un"`). Linhas em UoMs diferentes são convertidas via
  `product_uom._compute_quantity(qty, product.uom_id)` antes de somar.
- Quando o histórico é vazio na janela configurada, `action_generate` levanta
  `UserError` com mensagem clara — evita PDF em branco que parece bug.

## Envio por email

O wizard tem o Boolean `send_by_email`. Sem marcar, `action_generate` retorna o
`ir.actions.report` (download direto — comportamento das PRs anteriores). Quando
marcado:

1. `_render_qweb_pdf(report_name, wizard.ids)` gera os bytes do PDF do layout atual.
2. `ir.attachment.create` persiste o PDF ligado à `partner.commercial.condition`
   (`res_model='partner.commercial.condition'`, `res_id=condition.id`).
3. `mail.compose.message` é aberto via `ir.actions.act_window` com contexto:
   - `default_model='partner.commercial.condition'`, `default_res_id=condition.id` —
     email fica no chatter da condição.
   - `default_composition_mode='comment'`.
   - `default_template_id` + `default_use_template=True` — carrega o
     `email_template_pricelist`.
   - `default_attachment_ids=[attachment.id]` — lista de ids crus, não `[(4, id)]`
     (idiomático de defaults de contexto Odoo).

O template `mail.template` mora em `data/mail_template.xml` com `model_id` =
`partner.commercial.condition`. Não usa `report_template_ids` porque o report é
renderizado a partir do wizard transient — a anexação manual pelo `action_generate`
cobre isso sem gambiarra.

Partner sem email **não bloqueia**: o composer padrão do Odoo é a UI de revisão, e o
vendedor preenche/edita lá.

## Exclusão de categorias customizadas das listas gerais

Flag `tr_exclude_from_general_pricelist` em `product.category` (herdada pelo
`tr_pricelist_report` via `models/product_category.py`). Comportamento:

- Quando a flag está True numa categoria, produtos dessa categoria **e todas as
  descendentes** ficam fora das listagens gerais do relatório (`por_categoria` e
  `geralzao` Modo A/B).
- **Cascata rígida**: uma categoria filha não pode "desligar" o efeito herdado do pai.
  Solução operacional se precisar voltar: mover a filha pra outra árvore não flagada.
- O **layout histórico** (PR4) **ignora** a flag — cliente que comprou o produto
  customizado continua vendo o preço no histórico pra reorders.

### Implementação

`_resolve_products` monta o domain base (`active`, `sale_ok`, opcional filtro por
`category_ids`) e acrescenta `("categ_id", "not in", excluded_ids)` quando existem
categorias flagadas. A expansão é feita em lote em `_resolve_excluded_category_ids()`:

```python
flagged = env["product.category"].search([
    ("tr_exclude_from_general_pricelist", "=", True)
])
excluded = env["product.category"].search([
    ("id", "child_of", flagged.ids)
])
```

Duas queries, usa `parent_path` do Odoo (O(1)). `child_of` com lista vazia devolve
recordset vazio, então o helper é seguro quando nenhuma categoria está marcada.

### Cuidado operacional

A cascata é silenciosa: marcar na raiz "Produção Sob Encomenda" some com todos os
produtos descendentes das listas gerais sem UI específico indicando produto-por-produto.
O `help` do campo explica o efeito e o PDF do layout histórico continua mostrando os
produtos pra clientes que compraram — safety net. Se no futuro admins começarem a se
queixar de "sumiu produto", considerar um badge/flag read-only na form do produto
sinalizando a herança ("excluído via categoria X").

## Resolvedor de categoria

`_resolve_grouping_category(product, depth)` devolve a categoria na qual o produto é
agrupado. Clamping silencioso (nunca levanta):

- `depth = -1` → categoria folha (`product.categ_id`).
- `depth = -2` → pai da folha. **Default** do módulo.
- `depth = -N` acima da profundidade do produto → raiz da trilha.
- `depth = N` positivo → nível absoluto a partir da raiz.
- `depth = N` acima da profundidade do produto → folha (clamp).
- `product.categ_id` vazio → recordset vazio; na agregação vira seção com título em
  branco.

O clamping é **por produto**: cada um é clampado no contexto da sua própria árvore. Um
catálogo misto de produtos rasos e profundos aceita o mesmo `category_depth` sem
inconsistência.

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

- `tr_pricelist_report.validity_days` (default 30).
- `tr_pricelist_report.category_depth` (default `-2`, pai da folha). Aceita `-1`
  (folha), `-N` (N-ésimo ancestral, clampa à raiz), `N >= 0` (nível absoluto a partir da
  raiz, clampa à folha).
- `tr_pricelist_report.group_attribute_name` (default `"MARCA"`) — nome do
  `product.attribute` usado como eixo do Modo A.
- `tr_pricelist_report.group_attribute_id` — cache do id do atributo, resolvido pelo
  `post_init_hook` a partir do nome configurado. Vazio quando nenhum atributo bate (Modo
  A cai pro fallback por categoria).
- `tr_pricelist_report.history_months_back` (default 6) — janela em meses usada pelo
  layout `historico` pra varrer `sale.order.line`. Valor `0` desliga o layout
  (resolvedor curto-circuita e retorna vazio).

## Upgrades em devel (nota de operação)

Enquanto o stack de política comercial rodar só em `devel`, as mudanças de default via
`noupdate="1"` e a cache do `post_init_hook` não se propagam em `-u`. Pra aplicar
defaults novos (ex.: `category_depth` mudou de `-1` pra `-2` na PR2), **reinstalar o
módulo** (`-i tr_pricelist_report`) em vez de atualizar. Quando/se a família for pra
produção, migrations entram em cena e essa regra deixa de valer.

## Invariante

Paridade com `sale.order.line._compute_price_unit`: para o mesmo `(produto, condição)`,
o `price_unit` exibido no PDF é **idêntico** ao que o pedido calcularia. Testado em
`tests/test_pricing_consistency.py`.

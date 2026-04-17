# Guia de Uso — Commercial Policy

Módulo de política comercial para Odoo 16. Gerencia perfis de vendas, condições
comerciais, descontos estruturados, comissões dinâmicas e aprovações.

---

## Índice

1. [Conceitos Principais](#1-conceitos-principais)
2. [Configuração Inicial](#2-configuração-inicial)
3. [Fluxo do Pedido de Venda](#3-fluxo-do-pedido-de-venda)
4. [Canais de Desconto](#4-canais-de-desconto)
5. [Comissão Dinâmica (Agentes)](#5-comissão-dinâmica-agentes)
6. [Aprovação de Desconto Extra](#6-aprovação-de-desconto-extra)
7. [Salvar Condições no Cliente](#7-salvar-condições-no-cliente)
8. [Relatórios e Modo de Exibição](#8-relatórios-e-modo-de-exibição)
9. [Propagação para Fatura](#9-propagação-para-fatura)
10. [Parâmetros de Sistema](#10-parâmetros-de-sistema)
11. [Grupos de Segurança](#11-grupos-de-segurança)
12. [Validações e Restrições](#12-validações-e-restrições)

---

## 1. Conceitos Principais

### Perfil de Vendas (`tr.sales.profile`)

Define os limites de desconto e comissão para vendedores/agentes. Dois tipos:

| Tipo        | Uso                     | Recurso Especial                                                |
| ----------- | ----------------------- | --------------------------------------------------------------- |
| **Agente**  | Representantes externos | Bandas de comissão (comissão varia conforme desconto)           |
| **Interno** | Vendedores internos     | Bandas por valor de pedido (desconto máx. varia conforme valor) |

### Condição Comercial (`partner.commercial.condition`)

Cadastro de descontos e configurações para cada cliente. Uma por cliente por empresa.

### Regras do Perfil (`tr.sales.profile.rule`)

Limites de desconto por nível de produto:

- **Variante** (produto específico)
- **Template** (modelo de produto)
- **Categoria** (categoria de produto, percorre a árvore)
- **Geral** (fallback)

> **Precedência:** Variante > Template > Categoria > Geral

---

## 2. Configuração Inicial

### 2.1 Criar Perfis de Vendas

**Caminho:** Vendas > Configuração > Política Comercial > Perfis de Vendas

1. Crie um perfil (tipo Agente ou Interno)
2. Defina os limites globais:
   - **Desconto à Vista Máx.** — limite % de desconto à vista
   - **Desconto FOB Máx.** — limite % de desconto FOB
   - **Prazo Médio Máx. p/ Desc. à Vista** — nº máx. de dias médios do prazo de
     pagamento para permitir desconto à vista
   - **Limite Extra do Gerente** — até esse % de desconto extra, o gerente pode aprovar;
     acima, requer diretor
3. Adicione **regras** (aba "Rules"):
   - Escolha o nível (variante, template, categoria ou geral)
   - Defina o **Desconto Máx. do Vendedor** para cada regra
4. **Para perfis Agente:** adicione **Bandas de Comissão** em cada regra:
   - Ex.: Desconto até 5% → Comissão 10%; Desconto até 10% → Comissão 7%
   - A taxa de comissão deve ser **estritamente decrescente**
5. **Para perfis Interno:** adicione **Bandas de Valor de Pedido** em cada regra:
   - Ex.: Pedido ≥ R$ 1.000 → Desc. máx. 5%; ≥ R$ 5.000 → Desc. máx. 10%
   - O desconto máximo deve ser **estritamente crescente**

### 2.2 Atribuir Perfis

O perfil é atribuído ao pedido automaticamente, com a seguinte prioridade:

1. **Individual** — campo `sales_profile_id` no parceiro do vendedor (Contatos > aba
   Vendas)
2. **Equipe** — campo `sales_profile_id` na equipe de vendas (Vendas > Configuração >
   Equipes de Vendas)
3. **Global** — parâmetro de sistema `tr_commercial_policy.default_sales_profile_id`

> **Multi-empresa:** O campo `sales_profile_id` no parceiro é **company-dependent**
> (property). Isso significa que o mesmo vendedor pode ter perfis diferentes em cada
> empresa. O valor exibido/usado depende da empresa ativa do usuário.

### 2.3 Criar Condições Comerciais

**Caminho:** Vendas > Configuração > Política Comercial > Condições Comerciais

1. Selecione o cliente
2. Defina:
   - **Lista de Preços** — pricelist do cliente
   - **Retorno Contratual (%)** — ajusta o preço de referência
   - **Desconto à Vista (%)** — desconto geral à vista
   - **Desconto FOB (%)** — desconto geral FOB
   - **Desconto do Vendedor (%) - Geral** — desconto padrão para produtos sem linha
     específica
   - **Modo de Exibição** — como os preços aparecem nos relatórios impressos
3. Adicione **Linhas de Produto** para descontos específicos:
   - Por **template**: preencha apenas "Product Template"
   - Por **variante**: preencha "Product Template" + "Product Variant"
   - Cada linha tem `seller_discount` e `extra_discount` próprios

### 2.4 Vincular Condição ao Cliente

A condição é vinculada automaticamente ao pedido:

1. **Direta** — campo `commercial_condition_id` no parceiro
2. **Grupo Empresarial** — se o parceiro não tem condição, usa a do `company_group_id`
   (módulo `base_partner_company_group`)

> **Multi-empresa:** O campo `commercial_condition_id` no parceiro é
> **company-dependent** (property). Cada empresa pode ter uma condição comercial
> diferente para o mesmo cliente. O registro de condição comercial também possui
> `company_id` e a constraint de unicidade é por par (parceiro + empresa).

---

## 3. Fluxo do Pedido de Venda

```
┌─────────────────────────────────────────────────────────────┐
│                    CRIAR PEDIDO                              │
│  • Selecionar cliente e prazo de pagamento                  │
│  • Sistema atribui: perfil, condição, pricelist             │
│  • cash_discount e fob_discount preenchidos da condição     │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                  ADICIONAR LINHAS                             │
│  • base_price ← pricelist (com impostos)                    │
│  • reference_price ← base_price ajustado por retorno        │
│  • seller_discount ← condição (variante > template > geral) │
│  • price_unit = ref_price × (1 - (seller + extra) / 100)   │
│  • discount = cash_discount + fob_discount                  │
│  • commission_rate ← banda de comissão (apenas agentes)     │
└──────────────────────┬──────────────────────────────────────┘
                       │
        ┌──────────────┴──────────────┐
        │ Tem desconto extra?         │
        ├── NÃO → Confirmar           │
        └── SIM ↓                     │
┌──────────────────────────────────────────────────────────────┐
│          SOLICITAR APROVAÇÃO                                  │
│  • Preencher "Motivo do Desconto Extra" em cada linha       │
│  • Clicar "Request Discount Approval"                       │
│  • Gerente/Diretor aprova ou rejeita                        │
│  • Quando todos aprovados → confirmar pedido                │
└──────────────────────────────────────────────────────────────┘
```

---

## 4. Canais de Desconto

O módulo implementa 4 canais de desconto que se combinam:

### Canal 1: Retorno Contratual

- Definido na condição comercial do cliente
- Ajusta o **preço de referência** (não aparece como desconto na linha)
- Fórmula:
  ```
  preço_ref = (base_price × (1 - taxa_imposto - taxa_frete))
              ÷ (1 - retorno_contratual - taxa_imposto - taxa_frete)
  ```
- Taxas padrão: imposto = 18,05%, frete = 5% (configuráveis)

### Canal 2: Desconto do Vendedor (`seller_discount`)

- Aplicado **por linha** (vem da condição ou é ajustado manualmente)
- Limitado pelo `seller_discount_max` da regra do perfil
- **Afeta a comissão** do agente (quanto maior o desconto, menor a comissão)

### Canal 3: Desconto Extra (`extra_discount`)

- Desconto adicional por linha, acima do padrão
- **Requer aprovação** antes de confirmar o pedido
- **NÃO afeta** a comissão do agente

### Canal 4: Descontos à Vista / FOB

- Aplicados no **cabeçalho** do pedido, propagados para todas as linhas
- Limitados pelo perfil de vendas
- Desconto à vista condicionado ao prazo médio de pagamento
- **NÃO afetam** a comissão do agente

### Cálculo Final

```
price_unit = reference_price × (1 - (seller_discount + extra_discount) / 100)
discount (%) = cash_discount + fob_discount
valor_líquido = price_unit × qty × (1 - discount / 100)
```

---

## 5. Comissão Dinâmica (Agentes)

Para perfis do tipo **Agente**, a comissão varia conforme o desconto do vendedor.

### Como Funciona

1. O sistema resolve a **regra aplicável** para o produto da linha (variante >
   template > categoria > geral)
2. Busca nas **bandas de comissão** da regra a primeira banda onde
   `discount_up_to >= seller_discount`
3. Usa a `commission_rate` dessa banda

### Exemplo

| Banda | Desconto até | Comissão |
| ----- | ------------ | -------- |
| 1     | 5%           | 10%      |
| 2     | 10%          | 7%       |

- Vendedor dá 3% de desconto → comissão = **10%**
- Vendedor dá 7% de desconto → comissão = **7%**

### Regras Importantes

- Somente `seller_discount` afeta a comissão
- `extra_discount`, `cash_discount`, `fob_discount` **NÃO** alteram a comissão
- Se não houver bandas na regra, usa a comissão padrão do agente (fixada no cadastro)
- Perfis **internos** sempre usam a comissão padrão

### Campo `commission_rate`

- Calculado automaticamente na linha do pedido
- Visível na tree (coluna opcional "Commission Rate (%)")
- Propagado para a fatura via `tr_commission_rate`

---

## 6. Aprovação de Desconto Extra

### Solicitar Aprovação

1. No pedido em rascunho, certifique-se de que todas as linhas com `extra_discount > 0`
   tenham o campo **"Extra Discount Reason"** preenchido
2. Clique no botão **"Request Discount Approval"**
3. O sistema cria registros de aprovação para cada linha com desconto extra
4. O campo `approval_status` muda para **"Pending"**

### Nível de Aprovação

| Condição                                           | Nível Necessário |
| -------------------------------------------------- | ---------------- |
| `extra_discount` ≤ `manager_extra_limit` do perfil | Gerente          |
| `extra_discount` > `manager_extra_limit` do perfil | Diretor          |

### Aprovar / Rejeitar

**Caminho:** Vendas > Aprovações de Desconto

1. O gerente/diretor abre a aprovação pendente
2. Revisa o motivo e o desconto solicitado
3. Clica **"Approve"** ou **"Reject"**
   - Rejeição requer preenchimento do motivo

### Status do Pedido

| Status     | Significado                      |
| ---------- | -------------------------------- |
| `none`     | Sem descontos extras             |
| `pending`  | Aguardando aprovação             |
| `approved` | Todos aprovados — pode confirmar |
| `rejected` | Algum rejeitado — bloqueado      |

> O pedido **não pode ser confirmado** enquanto `approval_status ≠ approved` (quando há
> desconto extra).

---

## 7. Salvar Condições no Cliente

### Wizard Geral — "Save Conditions"

Botão no cabeçalho do pedido em rascunho. Compara os descontos do pedido com a condição
comercial salva e permite atualizar seletivamente.

1. Clique **"Save Conditions"** no pedido
2. O wizard mostra:
   - **Descontos gerais** (à vista, FOB, vendedor) — valor atual vs. pedido
   - **Linhas de produto** — apenas produtos com diferença
3. Marque os checkboxes dos descontos gerais que deseja atualizar
4. Para cada linha de produto:
   - Escolha **"Template"** ou **"Variant"** no campo "Save As"
   - Marque **"Ignore"** para não salvar
5. Clique **"Save to Customer"**

> Se o cliente não tiver condição comercial, o wizard cria uma automaticamente.

### Wizard por Linha — "Save to Customer"

Botão no formulário de cada linha do pedido. Salva rapidamente o desconto daquela linha.

1. Clique **"Save to Customer"** (ícone 💾) na linha
2. O wizard mostra:
   - Desconto atual vs. novo
   - Nível de origem (general / template / variant)
   - Opção de salvar como Template, Variant ou "Do Not Save"
3. Clique **"Save"**

---

## 8. Relatórios e Modo de Exibição

O campo `discount_display` na condição comercial controla colunas extras no relatório
impresso do pedido de venda.

| Modo               | Colunas no Relatório                                                                                |
| ------------------ | --------------------------------------------------------------------------------------------------- |
| **Show Discounts** | Descrição, Qtd, Preço Unitário, **Preço Referência**, **Desc. Vendedor %**, Disc.%, Impostos, Total |
| **Net Price Only** | Descrição, Qtd, Preço Unitário, Disc.%, Impostos, Total (padrão Odoo)                               |

As colunas **Preço Referência** e **Desconto do Vendedor** só aparecem quando o modo é
"Show Discounts".

---

## 9. Propagação para Fatura

Ao criar fatura a partir do pedido de venda, os seguintes campos são propagados:

### No cabeçalho (`account.move`)

| Campo                       | Origem                         |
| --------------------------- | ------------------------------ |
| `tr_cash_discount`      | `cash_discount` do pedido      |
| `tr_fob_discount`       | `fob_discount` do pedido       |
| `tr_contractual_return` | `contractual_return` do pedido |

### Nas linhas (`account.move.line`)

| Campo                    | Origem                               |
| ------------------------ | ------------------------------------ |
| `tr_commission_rate` | `commission_rate` da linha do pedido |

### Comissão na Fatura

A comissão do agente na fatura usa o `tr_commission_rate` propagado. Se o valor for
maior que zero, ele substitui a taxa fixa da comissão padrão.

---

## 10. Parâmetros de Sistema

Configuráveis em Definições > Técnico > Parâmetros > Parâmetros do Sistema:

| Parâmetro                                           | Padrão  | Descrição                                               |
| --------------------------------------------------- | ------- | ------------------------------------------------------- |
| `tr_commercial_policy.default_sales_profile_id` | `0`     | ID do perfil de vendas padrão global                    |
| `tr_commercial_policy.tax_rate_pct`             | `10.0` | Taxa de imposto (%) p/ cálculo do preço de referência   |
| `tr_commercial_policy.freight_rate_pct`         | `5.0`   | Taxa de frete (%) p/ cálculo do preço de referência     |
| `tr_commercial_policy.admin_rate_pct`           | `2.0`   | Taxa de despesa administrativa (%) p/ fator de correção |

Os três últimos são configurados via `Configuração → Vendas → Commercial Policy` e
armazenados em **percentual** (não decimal). As chaves antigas sem sufixo (`tax_rate`,
`freight_rate`) foram descontinuadas e não são mais lidas pelo módulo.

---

## 11. Grupos de Segurança

### Gerente de Vendas (`group_sales_manager`)

- Aprovar descontos extras até o limite do gerente
- Criar/editar condições comerciais
- Gerenciar aprovações de desconto

### Diretor de Vendas (`group_sales_director`)

- Herda permissões do gerente
- Aprovar descontos extras **acima** do limite do gerente
- Criar/editar perfis de vendas, regras e bandas

### Resumo de Acesso

| Modelo                 | Usuário Base | Gerente | Diretor |
| ---------------------- | :----------: | :-----: | :-----: |
| Perfis de Vendas       |      👁️      |   👁️    |   ✏️    |
| Regras do Perfil       |      👁️      |   👁️    |   ✏️    |
| Bandas de Comissão     |      👁️      |   👁️    |   ✏️    |
| Bandas de Valor        |      👁️      |   👁️    |   ✏️    |
| Condições Comerciais   |      👁️      |   ✏️    |   ✏️    |
| Linhas de Condição     |      👁️      |   ✏️    |   ✏️    |
| Aprovações de Desconto |      👁️      |   ✏️    |   ✏️    |
| Wizards de Salvamento  |      ✏️      |   ✏️    |   ✏️    |

👁️ = somente leitura, ✏️ = leitura/escrita/criação/exclusão

---

## 12. Validações e Restrições

### Perfil de Vendas

- Bandas de comissão: taxas devem ser **estritamente decrescentes** conforme desconto
  aumenta
- Bandas de valor: desconto máximo deve ser **estritamente crescente** conforme valor do
  pedido aumenta
- Regra tipo "produto" requer `product_id`; tipo "template" requer `product_tmpl_id`;
  tipo "categoria" requer `categ_id`

### Condição Comercial

- Uma condição por cliente por empresa (constraint SQL)
- Retorno contratual deve estar entre 0% e 100%
- Linhas de produto não podem ser duplicadas (mesmo produto/template na mesma condição)

### Pedido de Venda

- `cash_discount` ≤ `cash_discount_max` do perfil
- `fob_discount` ≤ `fob_discount_max` do perfil
- Desconto à vista bloqueado se prazo médio de pagamento > limite do perfil
- `seller_discount` ≤ `seller_discount_max` da regra aplicável (por linha)
- Desconto extra requer aprovação antes de confirmar
- Motivo obrigatório ao solicitar aprovação

---

## Dependências do Módulo

- `sale_management` — Vendas (Odoo padrão)
- `l10n_br_sale` — Localização brasileira para vendas
- `base_partner_company_group` — Grupos empresariais
- `sale_partner_company_group` — Grupo empresarial em vendas
- `engenere_commission` — Gestão de comissões (Engenere)

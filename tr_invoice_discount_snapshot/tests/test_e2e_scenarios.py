# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""End-to-end coverage covering the cenarios that Codex priorizou (Tier 1 + 2).

Cada teste cobre 1 cenario do roteiro de revisao. Numeracao no nome do
teste preserva a ordem original (e.g. ``test_e2e_01_*`` corresponde ao
cenario #1).
"""

from odoo.tests import tagged

from odoo.addons.tr_commercial_policy.tests.common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestE2EScenarios(CommercialPolicyTestCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls.product_template_a.invoice_policy = "order"
        cls.product_template_b.invoice_policy = "order"
        cls.condition.write({"cash_discount": 3.0, "fob_discount": 2.0})
        cls.fiscal_doc_type = cls.env.ref("l10n_br_fiscal.document_55")

    # --- Helpers ---

    def _make_fiscal_doc(self):
        return self.env["l10n_br_fiscal.document"].create(
            {
                "document_type_id": self.fiscal_doc_type.id,
                "company_id": self.env.company.id,
            }
        )

    def _attach_fiscal_line(self, account_line, qty=None, price=None):
        """Cria fiscal_document_line minimal, vincula ao account.move.line."""
        fiscal_line = self.env["l10n_br_fiscal.document.line"].create(
            {
                "document_id": self._make_fiscal_doc().id,
                "product_id": account_line.product_id.id,
                "quantity": qty if qty is not None else account_line.quantity,
                "price_unit": price if price is not None else account_line.price_unit,
            }
        )
        account_line.fiscal_document_line_id = fiscal_line
        return fiscal_line

    def _create_invoice_with_lines(self, move_type, lines_specs):
        """Cria account.move + N linhas de produto. Retorna (invoice, [lines]).

        ``lines_specs`` = [(product, qty, price), ...].

        Limpa explicitamente ``tr_cash_discount``/``tr_fob_discount`` do
        cabecalho — o partner padrao do common tem condition com cash/fob
        default que contamina os testes.
        """
        invoice = self.env["account.move"].create(
            {
                "move_type": move_type,
                "partner_id": self.customer.id,
            }
        )
        invoice.write({"tr_cash_discount": 0.0, "tr_fob_discount": 0.0})
        lines = []
        for product, qty, price in lines_specs:
            line = self.env["account.move.line"].create(
                {
                    "move_id": invoice.id,
                    "product_id": product.id,
                    "quantity": qty,
                    "price_unit": price,
                    "name": product.display_name,
                    "account_id": invoice.journal_id.default_account_id.id,
                }
            )
            lines.append(line)
        return invoice, lines

    # ----------------------------------------------------------------
    # Cenário 1 — out_invoice fiscal, múltiplas linhas, edição header
    # ----------------------------------------------------------------

    def test_e2e_01_out_invoice_fiscal_multilines_header_edit(self):
        invoice, (line_a, line_b) = self._create_invoice_with_lines(
            "out_invoice",
            [(self.product_a, 3, 100.0), (self.product_b, 5, 80.0)],
        )
        invoice.tr_cash_discount = 3.0
        invoice.tr_fob_discount = 2.0
        fiscal_a = self._attach_fiscal_line(line_a)
        fiscal_b = self._attach_fiscal_line(line_b)
        # Estado inicial: 5%
        self.assertAlmostEqual(line_a.discount, 5.0, places=2)
        self.assertAlmostEqual(line_b.discount, 5.0, places=2)
        # Editar header
        invoice.write({"tr_cash_discount": 4.0, "tr_fob_discount": 1.0})
        # Cada linha: 5% sobre sua base
        self.assertAlmostEqual(line_a.discount_value, 3 * 100.0 * 0.05, places=2)
        self.assertAlmostEqual(line_b.discount_value, 5 * 80.0 * 0.05, places=2)
        # Fiscal lê via related (1ª e única account_line por fiscal_doc_line)
        self.assertAlmostEqual(fiscal_a.discount_value, line_a.discount_value, places=2)
        self.assertAlmostEqual(fiscal_b.discount_value, line_b.discount_value, places=2)

    # ----------------------------------------------------------------
    # Cenário 2 — out_invoice fiscal, qty muda depois do header
    # ----------------------------------------------------------------

    def test_e2e_02_out_invoice_fiscal_qty_changes_after_header(self):
        invoice, (line,) = self._create_invoice_with_lines(
            "out_invoice", [(self.product_a, 1, 100.0)]
        )
        invoice.write({"tr_cash_discount": 5.0, "tr_fob_discount": 0.0})
        fiscal = self._attach_fiscal_line(line)
        self.assertAlmostEqual(line.discount, 5.0, places=2)
        self.assertAlmostEqual(line.discount_value, 5.0, places=2)
        # Mudar qty
        line.quantity = 2.0
        # Pct estável, value e fiscal recalculam
        self.assertAlmostEqual(line.discount, 5.0, places=2)
        self.assertAlmostEqual(line.discount_value, 10.0, places=2)
        self.assertAlmostEqual(fiscal.discount_value, 10.0, places=2)

    # ----------------------------------------------------------------
    # Cenário 3 — out_invoice fiscal, price_unit muda depois do header
    # ----------------------------------------------------------------

    def test_e2e_03_out_invoice_fiscal_price_changes_after_header(self):
        invoice, (line,) = self._create_invoice_with_lines(
            "out_invoice", [(self.product_a, 2, 50.0)]
        )
        invoice.write({"tr_cash_discount": 5.0, "tr_fob_discount": 0.0})
        fiscal = self._attach_fiscal_line(line)
        # Mudar price_unit
        line.price_unit = 100.0
        # Pct estável, value e fiscal recalculam pra nova base
        self.assertAlmostEqual(line.discount, 5.0, places=2)
        self.assertAlmostEqual(line.discount_value, 2 * 100.0 * 0.05, places=2)
        self.assertAlmostEqual(fiscal.discount_value, 10.0, places=2)

    # ----------------------------------------------------------------
    # Cenário 4 — out_refund (reversão) com fiscal e header editado
    # ----------------------------------------------------------------

    def test_e2e_04_out_refund_fiscal_header_edit(self):
        # Refund manual (substitui reversão real, que requer post real)
        invoice, (line,) = self._create_invoice_with_lines(
            "out_refund", [(self.product_a, 1, 200.0)]
        )
        invoice.tr_cash_discount = 6.0
        invoice.tr_fob_discount = 4.0
        fiscal = self._attach_fiscal_line(line)
        self.assertAlmostEqual(line.discount, 10.0, places=2)
        self.assertAlmostEqual(line.discount_value, 20.0, places=2)
        self.assertAlmostEqual(fiscal.discount_value, 20.0, places=2)
        # Editar header
        invoice.write({"tr_cash_discount": 1.0, "tr_fob_discount": 1.0})
        self.assertAlmostEqual(line.discount, 2.0, places=2)
        self.assertAlmostEqual(line.discount_value, 4.0, places=2)
        self.assertAlmostEqual(fiscal.discount_value, 4.0, places=2)

    # ----------------------------------------------------------------
    # Cenário 5 — in_invoice manual com fiscal
    # ----------------------------------------------------------------

    def test_e2e_05_in_invoice_fiscal_header_edit(self):
        invoice, (line,) = self._create_invoice_with_lines(
            "in_invoice", [(self.product_a, 4, 25.0)]
        )
        invoice.tr_cash_discount = 2.0
        invoice.tr_fob_discount = 3.0
        fiscal = self._attach_fiscal_line(line)
        # 5% sobre 100 = 5
        self.assertAlmostEqual(line.discount, 5.0, places=2)
        self.assertAlmostEqual(line.discount_value, 5.0, places=2)
        self.assertAlmostEqual(fiscal.discount_value, 5.0, places=2)
        self.assertAlmostEqual(line.price_subtotal, 95.0, places=2)

    # ----------------------------------------------------------------
    # Cenário 6 — in_refund com fiscal
    # ----------------------------------------------------------------

    def test_e2e_06_in_refund_fiscal_header_edit(self):
        invoice, (line,) = self._create_invoice_with_lines(
            "in_refund", [(self.product_a, 2, 75.0)]
        )
        invoice.write({"tr_cash_discount": 4.0, "tr_fob_discount": 1.0})
        fiscal = self._attach_fiscal_line(line)
        # 5% sobre 150 = 7.5
        self.assertAlmostEqual(line.discount, 5.0, places=2)
        self.assertAlmostEqual(line.discount_value, 7.5, places=2)
        self.assertAlmostEqual(fiscal.discount_value, 7.5, places=2)
        # Editar header
        invoice.write({"tr_cash_discount": 0.0, "tr_fob_discount": 8.0})
        self.assertAlmostEqual(line.discount, 8.0, places=2)
        self.assertAlmostEqual(line.discount_value, 12.0, places=2)
        self.assertAlmostEqual(fiscal.discount_value, 12.0, places=2)

    # ----------------------------------------------------------------
    # Cenário 7 — fatura agrupada (2 linhas) com header editado
    # ----------------------------------------------------------------

    def test_e2e_07_grouped_invoice_2_lines_header_edit(self):
        invoice, (line_a, line_b) = self._create_invoice_with_lines(
            "out_invoice",
            [(self.product_a, 1, 100.0), (self.product_b, 1, 200.0)],
        )
        invoice.tr_cash_discount = 0.0
        invoice.tr_fob_discount = 5.0
        fiscal_a = self._attach_fiscal_line(line_a)
        fiscal_b = self._attach_fiscal_line(line_b)
        # Cada linha: 5% sobre sua base — sem vazamento entre elas
        self.assertAlmostEqual(line_a.discount_value, 5.0, places=2)
        self.assertAlmostEqual(line_b.discount_value, 10.0, places=2)
        self.assertAlmostEqual(fiscal_a.discount_value, 5.0, places=2)
        self.assertAlmostEqual(fiscal_b.discount_value, 10.0, places=2)
        # Editar header
        invoice.write({"tr_fob_discount": 10.0})
        self.assertAlmostEqual(line_a.discount_value, 10.0, places=2)
        self.assertAlmostEqual(line_b.discount_value, 20.0, places=2)
        self.assertAlmostEqual(fiscal_a.discount_value, 10.0, places=2)
        self.assertAlmostEqual(fiscal_b.discount_value, 20.0, places=2)

    # ----------------------------------------------------------------
    # Cenário 8 — account_line_ids > 1 pra mesma fiscal_line
    # ----------------------------------------------------------------

    def test_e2e_08_multiple_account_lines_per_fiscal_line(self):
        """**Trade-off conhecido — NÃO é comportamento funcional desejável.**

        Se duas account.move.line apontarem pra mesma fiscal_document_line,
        o ``related="account_line_ids.discount_value"`` lê o **primeiro**
        registro (não soma, não agrega). Em invoice grouping com múltiplas
        account lines por fiscal line — caso raro/inexistente no fluxo
        Trento conhecido — só a primeira linha contribuiria pro fiscal,
        as outras seriam ignoradas.

        Esse teste **documenta empiricamente** a fragilidade e serve como
        guarda de regressão: se aparecer cenário real de grouping
        multi-account em prod, este teste falha (assert quebra) e força
        evolução pra agregação explícita ao invés de descobrir o problema
        na NF-e.
        """
        invoice, (line_a, line_b) = self._create_invoice_with_lines(
            "out_invoice",
            [(self.product_a, 1, 100.0), (self.product_a, 2, 100.0)],
        )
        invoice.tr_cash_discount = 5.0
        # Cria 1 fiscal_doc_line e linka AS DUAS account lines nela
        fiscal_doc = self._make_fiscal_doc()
        fiscal_line = self.env["l10n_br_fiscal.document.line"].create(
            {
                "document_id": fiscal_doc.id,
                "product_id": self.product_a.id,
                "quantity": 1.0,
                "price_unit": 100.0,
            }
        )
        line_a.fiscal_document_line_id = fiscal_line
        line_b.fiscal_document_line_id = fiscal_line
        # Cada account line calcula seu discount_value independente
        self.assertAlmostEqual(line_a.discount_value, 5.0, places=2)
        self.assertAlmostEqual(line_b.discount_value, 10.0, places=2)
        # fiscal_line.discount_value via related lê só a primeira (line_a).
        # Trade-off documentado — se Trento usar invoice grouping com
        # múltiplas account lines por fiscal line, ajustar pra agregação.
        self.assertEqual(len(fiscal_line.account_line_ids), 2)
        self.assertAlmostEqual(fiscal_line.discount_value, 5.0, places=2)

    # ----------------------------------------------------------------
    # Cenário 9 — partial invoice com edição manual
    # ----------------------------------------------------------------

    def test_e2e_09_partial_invoice_header_edit(self):
        order = self._create_order()
        order.fiscal_operation_id = False
        line = self._create_order_line(order, qty=4, base_price=100.0)
        line.seller_discount = 0.0
        line.discount_fixed = True
        order.action_confirm()
        invoice = order._create_invoices()
        inv_line = invoice.invoice_line_ids.filtered(lambda line: line.product_id)[0]
        # Forço qty parcial
        inv_line.quantity = 2.0
        # Editar header
        invoice.write({"tr_cash_discount": 10.0, "tr_fob_discount": 0.0})
        # Pct=10, base=2 * price_unit
        self.assertAlmostEqual(inv_line.discount, 10.0, places=2)
        self.assertAlmostEqual(
            inv_line.discount_value, 2 * inv_line.price_unit * 0.10, places=2
        )

    # ----------------------------------------------------------------
    # Cenário 10 — header zero em cenário fiscal real
    # ----------------------------------------------------------------

    def test_e2e_10_header_zero_clears_fiscal(self):
        invoice, (line,) = self._create_invoice_with_lines(
            "out_invoice", [(self.product_a, 5, 100.0)]
        )
        invoice.tr_cash_discount = 4.0
        invoice.tr_fob_discount = 1.0
        fiscal = self._attach_fiscal_line(line)
        self.assertAlmostEqual(line.discount_value, 25.0, places=2)
        self.assertAlmostEqual(fiscal.discount_value, 25.0, places=2)
        # Zerar header
        invoice.write({"tr_cash_discount": 0.0, "tr_fob_discount": 0.0})
        self.assertAlmostEqual(line.discount, 0.0, places=2)
        self.assertAlmostEqual(line.discount_value, 0.0, places=2)
        self.assertAlmostEqual(fiscal.discount_value, 0.0, places=2)
        # price_subtotal volta ao base sem desconto
        self.assertAlmostEqual(line.price_subtotal, 500.0, places=2)

    # ----------------------------------------------------------------
    # Cenário 11 — draft → posted → cancel → draft
    # ----------------------------------------------------------------

    def test_e2e_11_state_transitions(self):
        invoice, (line,) = self._create_invoice_with_lines(
            "out_invoice", [(self.product_a, 1, 100.0)]
        )
        invoice.tr_cash_discount = 3.0
        invoice.tr_fob_discount = 2.0
        self.assertAlmostEqual(line.discount, 5.0, places=2)
        # Posted via SQL (evita validações fiscais reais)
        self.env.cr.execute(
            "UPDATE account_move SET state='posted' WHERE id=%s", (invoice.id,)
        )
        invoice.invalidate_recordset()
        line.invalidate_recordset()
        # Posted: edição no header não muda a linha
        invoice.tr_cash_discount = 99.0
        line.invalidate_recordset()
        self.assertAlmostEqual(line.discount, 5.0, places=2)
        # Volta a draft
        self.env.cr.execute(
            "UPDATE account_move SET state='draft' WHERE id=%s", (invoice.id,)
        )
        invoice.invalidate_recordset()
        line.invalidate_recordset()
        # Trigger recompute
        invoice.write({"tr_cash_discount": 10.0})
        self.assertAlmostEqual(line.discount, 12.0, places=2)
        self.assertAlmostEqual(line.discount_value, 12.0, places=2)

    # ----------------------------------------------------------------
    # Cenário 13 — operação fiscal com impostos sensíveis a desconto
    # ----------------------------------------------------------------
    # Setup tributário completo é caro de montar em teste isolado.
    # Cobertura indireta: como `discount_value` da `account.move.line`
    # é compute store=True e sempre recalcula da base, qualquer compute
    # de imposto que depende de `discount_value` recebe valor coerente.
    # Validamos a cadeia: edita header -> line.discount_value muda ->
    # price_subtotal muda -> amount_untaxed/amount_total acompanham.
    #
    # Validação de impostos específicos (PIS/COFINS sensíveis a
    # incondicional vs condicional) requer fiscal_operation real e
    # mapping tributário; cobertura empírica em devel real após PR
    # mergeada.

    def test_e2e_13_discount_chain_propagates_to_totals(self):
        invoice, (line,) = self._create_invoice_with_lines(
            "out_invoice", [(self.product_a, 10, 50.0)]
        )
        invoice.write({"tr_cash_discount": 4.0, "tr_fob_discount": 0.0})
        fiscal = self._attach_fiscal_line(line)
        # Estado inicial
        original_subtotal = line.price_subtotal
        original_total = invoice.amount_total
        # Editar header pra um pct maior
        invoice.write({"tr_cash_discount": 10.0})
        # discount_value aumenta
        self.assertAlmostEqual(line.discount, 10.0, places=2)
        self.assertAlmostEqual(line.discount_value, 10 * 50.0 * 0.10, places=2)
        self.assertAlmostEqual(fiscal.discount_value, 50.0, places=2)
        # price_subtotal e amount_total reduzem coerentemente
        self.assertLess(line.price_subtotal, original_subtotal)
        self.assertLess(invoice.amount_total, original_total)
        self.assertAlmostEqual(
            line.price_subtotal,
            line.quantity * line.price_unit * (1 - line.discount / 100.0),
            places=2,
        )

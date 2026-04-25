# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from odoo.addons.tr_commercial_policy.tests.common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestInvoiceDiscountSnapshot(CommercialPolicyTestCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls.product_template_a.invoice_policy = "order"
        # Garantir que a condition tem cash/fob distintos para os testes
        cls.condition.write(
            {
                "cash_discount": 3.0,
                "fob_discount": 2.0,
            }
        )

    def _make_invoice_no_fiscal(self, qty=10, base_price=100.0):
        """Cria sale.order sem fiscal_operation_id, confirma e gera fatura.

        Reproduz o fluxo TREINAMENTO (sandbox sem config fiscal).
        """
        order = self._create_order()
        order.fiscal_operation_id = False
        line = self._create_order_line(order, qty=qty, base_price=base_price)
        line.seller_discount = 0.0
        line.extra_discount = 0.0
        line.discount_fixed = True
        order.action_confirm()
        invoice = order._create_invoices()
        return order, invoice

    # --- Bug alvo: cenário TREINAMENTO (sem fiscal) ---

    def test_invoice_no_fiscal_uses_header_discount(self):
        """Linha sem fiscal aplica cash+fob do header (bug que originou o módulo)."""
        _order, invoice = self._make_invoice_no_fiscal(qty=10)
        line = invoice.invoice_line_ids.filtered(lambda line: line.product_id)
        self.assertTrue(line, "fatura deve ter linha de produto")
        line = line[0]
        self.assertFalse(line.fiscal_document_line_id, "linha deve estar sem fiscal")
        self.assertAlmostEqual(line.discount, 5.0, places=2)
        expected_value = (line.quantity or 0) * (line.price_unit or 0) * 0.05
        self.assertAlmostEqual(line.discount_value, expected_value, places=2)

    def test_subtotal_coherent_with_discount(self):
        """price_subtotal aplica o discount calculado pelo módulo."""
        _order, invoice = self._make_invoice_no_fiscal(qty=10)
        line = invoice.invoice_line_ids.filtered(lambda line: line.product_id)[0]
        expected_subtotal = (
            line.quantity * line.price_unit * (1 - line.discount / 100.0)
        )
        self.assertAlmostEqual(line.price_subtotal, expected_subtotal, places=2)

    # --- Recompute ao mudar header em draft ---

    def test_recompute_when_header_changes_in_draft(self):
        """Mudar tr_cash_discount em fatura draft → linhas recalculam.

        Usa ``invoice.write(...)`` (caminho ORM persistente), não assignment,
        para exercitar o fluxo real de edição via UI/API.
        """
        _order, invoice = self._make_invoice_no_fiscal(qty=10)
        line = invoice.invoice_line_ids.filtered(lambda line: line.product_id)[0]
        # Estado inicial: 3 + 2 = 5
        self.assertAlmostEqual(line.discount, 5.0, places=2)
        # Mudar o header via ORM write
        invoice.write({"tr_cash_discount": 10.0})
        # 10 + 2 = 12
        self.assertAlmostEqual(line.discount, 12.0, places=2)
        expected_value = line.quantity * line.price_unit * 0.12
        self.assertAlmostEqual(line.discount_value, expected_value, places=2)

    # --- Faturamento parcial ---

    def test_partial_invoicing_recalculates_value(self):
        """Faturamento parcial: discount_value bate com a qty da fatura, não da venda."""
        order = self._create_order()
        order.fiscal_operation_id = False
        line = self._create_order_line(order, qty=4, base_price=100.0)
        line.seller_discount = 0.0
        line.discount_fixed = True
        order.action_confirm()
        # Faturamento parcial: cria invoice e força qty=2 na linha
        # (invoice_policy=order traz qty=4 do pedido por padrão).
        invoice = order._create_invoices()
        inv_line = invoice.invoice_line_ids.filtered(lambda line: line.product_id)[0]
        # Forço qty=2 simulando faturamento parcial
        inv_line.quantity = 2.0
        # Recompute deve aplicar 5% sobre 2 * price_unit
        expected_value = 2.0 * inv_line.price_unit * 0.05
        self.assertAlmostEqual(inv_line.discount_value, expected_value, places=2)
        self.assertAlmostEqual(inv_line.discount, 5.0, places=2)

    # --- Header zerado: sem política ---

    def test_header_zero_no_preexisting_discount_yields_zero(self):
        """Sem política e sem discount preexistente → tudo zero."""
        invoice = self._create_manual_invoice()
        invoice.tr_cash_discount = 0.0
        invoice.tr_fob_discount = 0.0
        # Adicionar linha de produto manualmente
        line = self.env["account.move.line"].create(
            {
                "move_id": invoice.id,
                "product_id": self.product_a.id,
                "quantity": 5.0,
                "price_unit": 100.0,
                "name": "Manual line",
                "account_id": invoice.journal_id.default_account_id.id,
            }
        )
        line._compute_tr_discount()
        self.assertAlmostEqual(line.discount, 0.0, places=2)
        self.assertAlmostEqual(line.discount_value, 0.0, places=2)

    # --- Gate 1: posted é imutável ---

    def test_posted_state_protects_history(self):
        """Mudar header em fatura posted → linha NÃO recalcula."""
        _order, invoice = self._make_invoice_no_fiscal(qty=10)
        line = invoice.invoice_line_ids.filtered(lambda line: line.product_id)[0]
        original_discount = line.discount
        original_value = line.discount_value
        # Snapshot antes do post
        # Forçar post (pode falhar por validações fiscais; usa SQL direto pra simular)
        self.env.cr.execute(
            "UPDATE account_move SET state='posted' WHERE id=%s", (invoice.id,)
        )
        invoice.invalidate_recordset()
        # Mudar header não deve afetar a linha
        invoice.tr_cash_discount = 99.0
        line.invalidate_recordset()
        # Re-leitura: o gate posted impede o recompute
        self.assertAlmostEqual(line.discount, original_discount, places=2)
        self.assertAlmostEqual(line.discount_value, original_value, places=2)

    # --- Gate 2: preserva discount preexistente quando header está zerado ---

    def test_no_policy_preserves_existing_discount(self):
        """Linha draft com discount preexistente, header zero → preserva pct."""
        invoice = self._create_manual_invoice()
        invoice.tr_cash_discount = 0.0
        invoice.tr_fob_discount = 0.0
        line = self.env["account.move.line"].create(
            {
                "move_id": invoice.id,
                "product_id": self.product_a.id,
                "quantity": 1.0,
                "price_unit": 100.0,
                "discount": 7.0,  # discount preexistente
                "name": "Manual line w/ discount",
                "account_id": invoice.journal_id.default_account_id.id,
            }
        )
        # Recompute manual: deve preservar o 7.0
        line._compute_tr_discount()
        self.assertAlmostEqual(line.discount, 7.0, places=2)
        # E discount_value deve estar coerente
        self.assertAlmostEqual(line.discount_value, 1.0 * 100.0 * 0.07, places=2)

    def test_gate2_recalculates_value_on_qty_change(self):
        """Gate 2 + mudança de qty: discount_value recalcula com base nova."""
        invoice = self._create_manual_invoice()
        invoice.tr_cash_discount = 0.0
        invoice.tr_fob_discount = 0.0
        line = self.env["account.move.line"].create(
            {
                "move_id": invoice.id,
                "product_id": self.product_a.id,
                "quantity": 1.0,
                "price_unit": 100.0,
                "discount": 5.0,
                "name": "Manual line",
                "account_id": invoice.journal_id.default_account_id.id,
            }
        )
        # Recompute inicial estabiliza
        line._compute_tr_discount()
        # Mudar quantity
        line.quantity = 2.0
        line._compute_tr_discount()
        self.assertAlmostEqual(line.discount, 5.0, places=2)
        # discount_value deve ter sido recalculado para a nova base
        self.assertAlmostEqual(line.discount_value, 2.0 * 100.0 * 0.05, places=2)

    # --- Caminho fiscal: documento fiscal manda ---

    def test_fiscal_document_line_overrides_header(self):
        """fiscal_document_line_id presente → discount_value vem do fiscal,
        ignora header da política comercial.
        """
        invoice = self._create_manual_invoice()
        invoice.tr_cash_discount = 99.0  # header divergente do fiscal
        invoice.tr_fob_discount = 0.0
        line = self.env["account.move.line"].create(
            {
                "move_id": invoice.id,
                "product_id": self.product_a.id,
                "quantity": 1.0,
                "price_unit": 100.0,
                "name": "Fiscal line",
                "account_id": invoice.journal_id.default_account_id.id,
            }
        )
        # Cria fiscal document line mínima e vincula
        fiscal_doc = self.env["l10n_br_fiscal.document"].create(
            {
                "document_type_id": self.env.ref("l10n_br_fiscal.document_55").id,
                "company_id": self.env.company.id,
            }
        )
        fiscal_line = self.env["l10n_br_fiscal.document.line"].create(
            {
                "document_id": fiscal_doc.id,
                "product_id": self.product_a.id,
                "quantity": 1.0,
                "price_unit": 100.0,
                "discount_value": 10.0,  # fiscal define R$ 10 de desconto
            }
        )
        line.fiscal_document_line_id = fiscal_line
        line._compute_tr_discount()
        # Linha deve usar fiscal (10), ignorando header (99)
        self.assertAlmostEqual(line.discount_value, 10.0, places=2)
        # discount = 10 * 100 / (1 * 100) = 10%
        self.assertAlmostEqual(line.discount, 10.0, places=2)

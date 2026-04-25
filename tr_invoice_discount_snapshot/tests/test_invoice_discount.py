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

    # --- Bug alvo: cenário sem fiscal aplica header ---

    def test_invoice_no_fiscal_uses_header_discount(self):
        """Linha sem fiscal aplica cash+fob do header."""
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

    def test_qty_change_recomputes_value(self):
        """Mudança de qty em draft recalcula discount_value (pct estável)."""
        _order, invoice = self._make_invoice_no_fiscal(qty=4, base_price=100.0)
        inv_line = invoice.invoice_line_ids.filtered(lambda line: line.product_id)[0]
        self.assertAlmostEqual(inv_line.discount, 5.0, places=2)
        original_value = inv_line.discount_value
        # Reduz qty
        inv_line.quantity = 2.0
        # Pct estável, value recalculado pra nova base
        self.assertAlmostEqual(inv_line.discount, 5.0, places=2)
        self.assertAlmostEqual(
            inv_line.discount_value, 2.0 * inv_line.price_unit * 0.05, places=2
        )
        self.assertNotAlmostEqual(inv_line.discount_value, original_value, places=2)

    # --- Faturamento parcial ---

    def test_partial_invoicing_recalculates_value(self):
        """Faturamento parcial: discount_value bate com a qty da fatura, não da venda."""
        order = self._create_order()
        order.fiscal_operation_id = False
        line = self._create_order_line(order, qty=4, base_price=100.0)
        line.seller_discount = 0.0
        line.discount_fixed = True
        order.action_confirm()
        invoice = order._create_invoices()
        inv_line = invoice.invoice_line_ids.filtered(lambda line: line.product_id)[0]
        # Forço qty=2 simulando faturamento parcial
        inv_line.quantity = 2.0
        expected_value = 2.0 * inv_line.price_unit * 0.05
        self.assertAlmostEqual(inv_line.discount_value, expected_value, places=2)
        self.assertAlmostEqual(inv_line.discount, 5.0, places=2)

    # --- Header zerado limpa o desconto ---

    def test_header_zero_clears_discount(self):
        """Header zerado em qualquer momento -> linha vai pra zero."""
        _order, invoice = self._make_invoice_no_fiscal(qty=10)
        line = invoice.invoice_line_ids.filtered(lambda line: line.product_id)[0]
        self.assertAlmostEqual(line.discount, 5.0, places=2)
        invoice.write({"tr_cash_discount": 0.0, "tr_fob_discount": 0.0})
        self.assertAlmostEqual(line.discount, 0.0, places=2)
        self.assertAlmostEqual(line.discount_value, 0.0, places=2)

    # --- Gate posted: histórico imutável ---

    def test_posted_state_protects_history(self):
        """Mudar header em fatura posted → linha NÃO recalcula."""
        _order, invoice = self._make_invoice_no_fiscal(qty=10)
        line = invoice.invoice_line_ids.filtered(lambda line: line.product_id)[0]
        original_discount = line.discount
        original_value = line.discount_value
        self.env.cr.execute(
            "UPDATE account_move SET state='posted' WHERE id=%s", (invoice.id,)
        )
        invoice.invalidate_recordset()
        # Mudar header não deve afetar a linha
        invoice.tr_cash_discount = 99.0
        line.invalidate_recordset()
        self.assertAlmostEqual(line.discount, original_discount, places=2)
        self.assertAlmostEqual(line.discount_value, original_value, places=2)

    # --- Caminho fiscal: account é fonte, fiscal lê via related ---

    def test_account_discount_value_propagates_to_fiscal(self):
        """fiscal_document_line.discount_value reflete account_line.discount_value
        via related (l10n_br_fiscal_document_line override).
        """
        invoice = self._create_manual_invoice()
        invoice.tr_cash_discount = 8.0
        invoice.tr_fob_discount = 2.0
        line = self.env["account.move.line"].create(
            {
                "move_id": invoice.id,
                "product_id": self.product_a.id,
                "quantity": 5.0,
                "price_unit": 100.0,
                "name": "Fiscal line",
                "account_id": invoice.journal_id.default_account_id.id,
            }
        )
        # Cria fiscal document line e vincula
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
                "quantity": 5.0,
                "price_unit": 100.0,
            }
        )
        line.fiscal_document_line_id = fiscal_line
        line._compute_tr_discount()
        # Header (10%) sobre base (5 * 100 = 500) = 50
        expected = 50.0
        self.assertAlmostEqual(line.discount_value, expected, places=2)
        # Fiscal lê do account via related
        self.assertAlmostEqual(fiscal_line.discount_value, expected, places=2)

    def test_fiscal_value_changes_when_header_changes(self):
        """Editar header em draft propaga pro fiscal via related."""
        invoice = self._create_manual_invoice()
        invoice.tr_cash_discount = 5.0
        invoice.tr_fob_discount = 0.0
        line = self.env["account.move.line"].create(
            {
                "move_id": invoice.id,
                "product_id": self.product_a.id,
                "quantity": 2.0,
                "price_unit": 50.0,
                "name": "Header-driven line",
                "account_id": invoice.journal_id.default_account_id.id,
            }
        )
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
                "quantity": 2.0,
                "price_unit": 50.0,
            }
        )
        line.fiscal_document_line_id = fiscal_line
        line._compute_tr_discount()
        # Edita header
        invoice.write({"tr_cash_discount": 20.0})
        # Compute roda novamente
        # base = 100, pct = 20 → 20
        self.assertAlmostEqual(line.discount_value, 20.0, places=2)
        self.assertAlmostEqual(fiscal_line.discount_value, 20.0, places=2)

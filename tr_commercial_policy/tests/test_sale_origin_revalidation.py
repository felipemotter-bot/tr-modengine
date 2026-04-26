# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""Cobertura ponta-a-ponta para revalidação Classe A em sale-origin.

Cenários reais que motivaram o fix do skip em ``_compute_price_unit`` e o
ajuste do ``action_resync_from_sale_order``:

- Operador abre fatura draft sale-origin, edita ``seller_discount`` /
  ``extra_discount`` esperando que ``price_unit`` caia/suba conforme a
  fórmula da política. Antes do fix, ``price_unit`` ficava stale; agora
  recompute via ``calc_price_unit(reference, seller, extra)``.
- Resync do pedido restaura ``price_unit`` em adição aos demais campos
  snapshot, evitando divergência residual após o operador clicar em
  "Restaurar do Pedido".
- ``commission_rate`` divergente (efeito derivado de seller edit) entra
  em Classe A — não deve cair em hard-block no post.
- Snapshot rígido ainda vale para ``base_price`` / ``reference_price`` /
  ``agent_ids``: edits diretos não acontecem por compute em sale-origin.
- Posted é imutável.
"""

from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestSaleOriginRevalidation(CommercialPolicyTestCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()
        cls.product_template_a.invoice_policy = "order"
        cls.product_template_b.invoice_policy = "order"

    # --- Helpers ---

    def _make_invoice_from_order(self, seller_discount=0.0, qty=10):
        order, invoice = self._create_confirmed_order_with_invoice(
            seller_discount=seller_discount, qty=qty
        )
        line = invoice.invoice_line_ids.filtered(
            lambda sol: sol.display_type == "product"
        )[0]
        return order, invoice, line

    # --- Edição de seller/extra recomputa price_unit ---

    def test_e2e_seller_edit_recomputes_price_unit(self):
        from ..models.policy_utils import calc_price_unit

        _order, _invoice, line = self._make_invoice_from_order(
            seller_discount=0.0, qty=5
        )
        original_pu = line.price_unit
        line.write({"seller_discount": 10.0})
        line.invalidate_recordset(["price_unit"])
        expected = calc_price_unit(
            line.reference_price, line.seller_discount, line.extra_discount
        )
        self.assertAlmostEqual(line.price_unit, expected, delta=0.02)
        self.assertNotAlmostEqual(line.price_unit, original_pu, delta=0.02)

    def test_e2e_extra_edit_recomputes_price_unit(self):
        from ..models.policy_utils import calc_price_unit

        _order, _invoice, line = self._make_invoice_from_order(
            seller_discount=0.0, qty=5
        )
        line.write({"extra_discount": 5.0})
        line.invalidate_recordset(["price_unit"])
        expected = calc_price_unit(
            line.reference_price, line.seller_discount, line.extra_discount
        )
        self.assertAlmostEqual(line.price_unit, expected, delta=0.02)

    def test_e2e_combined_seller_extra_edit_recomputes(self):
        """Padrão do bug original: seller + extra combinados recomputam pu.

        Setup do test common limita seller a 10%, então usa valores dentro
        do limite — o objetivo é validar que ``calc_price_unit`` aplica os
        dois descontos juntos, não reproduzir os números exatos do caso.
        """
        from ..models.policy_utils import calc_price_unit

        _order, _invoice, line = self._make_invoice_from_order(
            seller_discount=0.0, qty=100
        )
        line.write({"seller_discount": 8.0, "extra_discount": 12.0})
        line.invalidate_recordset(["price_unit"])
        expected = calc_price_unit(
            line.reference_price, line.seller_discount, line.extra_discount
        )
        self.assertAlmostEqual(line.price_unit, expected, delta=0.02)

    # --- Resync restaura price_unit ---

    def test_e2e_resync_restores_price_unit(self):
        """Após editar seller, clicar 'Restaurar do Pedido' volta price_unit."""
        from ..models.policy_utils import calc_price_unit

        order, invoice, line = self._make_invoice_from_order(seller_discount=0.0, qty=5)
        sale_line = order.order_line[0]
        original_resynced = calc_price_unit(
            sale_line.reference_price,
            sale_line.seller_discount,
            sale_line.extra_discount,
        )
        # Operador edita (dentro do limite de 10% do setup)
        line.write({"seller_discount": 8.0})
        line.invalidate_recordset(["price_unit"])
        self.assertNotAlmostEqual(line.price_unit, original_resynced, delta=0.02)
        # Resync
        invoice.action_resync_from_sale_order()
        line.invalidate_recordset()
        self.assertAlmostEqual(
            line.seller_discount, sale_line.seller_discount, delta=0.01
        )
        self.assertAlmostEqual(line.price_unit, original_resynced, delta=0.02)

    # --- Banner de divergência ---

    def test_e2e_divergence_warning_silent_when_aligned(self):
        """Sem edição manual, banner fica vazio (alinhado com pedido)."""
        _order, invoice, _line = self._make_invoice_from_order(qty=3)
        # invoice_divergence_warning é compute non-stored — força recompute
        invoice.invalidate_recordset(["invoice_divergence_warning"])
        self.assertFalse(invoice.invoice_divergence_warning)

    # --- Snapshot rígido permanece (Classe B) ---

    def test_e2e_base_price_still_frozen_in_sale_origin(self):
        """base_price em sale-origin não é recomputado pelo policy chain."""
        _order, _invoice, line = self._make_invoice_from_order(qty=5)
        snapshot_base = line.base_price
        # Force compute trigger
        line.invalidate_recordset(["base_price"])
        line._compute_base_price()
        self.assertAlmostEqual(line.base_price, snapshot_base, delta=0.01)

    def test_e2e_reference_price_still_frozen_in_sale_origin(self):
        """reference_price em sale-origin não é recomputado."""
        _order, _invoice, line = self._make_invoice_from_order(qty=5)
        snapshot_ref = line.reference_price
        line.invalidate_recordset(["reference_price"])
        line._compute_reference_price()
        self.assertAlmostEqual(line.reference_price, snapshot_ref, delta=0.01)

    # --- Refund sale-origin também recompute price_unit ---

    def test_e2e_seller_edit_recomputes_price_unit_in_out_refund(self):
        """Refund draft sale-origin: edit de seller também recompute pu.

        Refund é "livre de governança" no post (action_post não chama
        ``_check_invoice_policy``), mas o recompute de ``price_unit`` no
        draft segue a mesma filosofia do invoice: editar desconto e o
        valor não mudar é incoerente. Operador edita, política recalcula.
        """
        from ..models.policy_utils import calc_price_unit

        _order, invoice, _line = self._make_invoice_from_order(qty=5)
        invoice.action_post()
        wizard = (
            self.env["account.move.reversal"]
            .with_context(active_model="account.move", active_ids=invoice.ids)
            .create({"reason": "Test", "journal_id": invoice.journal_id.id})
        )
        action = wizard.reverse_moves()
        refund = self.env["account.move"].browse(action["res_id"])
        refund_line = refund.invoice_line_ids.filtered(
            lambda sol: sol.display_type == "product"
        )[0]
        refund_line.write({"seller_discount": 7.5})
        refund_line.invalidate_recordset(["price_unit"])
        expected = calc_price_unit(
            refund_line.reference_price,
            refund_line.seller_discount,
            refund_line.extra_discount,
        )
        self.assertAlmostEqual(refund_line.price_unit, expected, delta=0.02)

    # --- Resync mantém totals coerentes ---

    def test_e2e_resync_keeps_totals_consistent(self):
        """Após resync, ``price_subtotal`` e ``amount_total`` batem com pu novo.

        ``action_resync_from_sale_order`` escreve ``price_unit`` sob
        ``skip_invoice_sync=True``. Esse teste prova que totais/impostos
        não ficam stale após o resync — o flush+invalidate garante que
        o compute monetário rodou com o novo valor.
        """
        order, invoice, line = self._make_invoice_from_order(seller_discount=0.0, qty=5)
        sale_line = order.order_line[0]
        # Operador edita seller, totais recalculam pelo cascade do compute
        line.write({"seller_discount": 8.0})
        # Resync volta tudo à origem
        invoice.action_resync_from_sale_order()
        invoice.invalidate_recordset()
        line.invalidate_recordset()
        # Após resync, price_subtotal deve ser consistente com price_unit * qty
        # (ignorando taxas para o assert; calc é o mesmo do core)
        expected_subtotal = line.price_unit * line.quantity * (1 - line.discount / 100)
        self.assertAlmostEqual(line.price_subtotal, expected_subtotal, delta=0.05)
        # E o amount_total da invoice deve refletir as linhas
        sum_lines = sum(
            inv_line.price_subtotal
            for inv_line in invoice.invoice_line_ids
            if inv_line.display_type == "product"
        )
        self.assertAlmostEqual(invoice.amount_untaxed, sum_lines, delta=0.05)
        # amount_total = amount_untaxed + amount_tax (cobre o fluxo do
        # ``skip_invoice_sync=True`` que poderia mascarar tax lines stale)
        self.assertAlmostEqual(
            invoice.amount_total,
            invoice.amount_untaxed + invoice.amount_tax,
            delta=0.05,
        )
        # Sanity: price_unit pós-resync = calc do sale_line
        from ..models.policy_utils import calc_price_unit

        expected_pu = calc_price_unit(
            sale_line.reference_price,
            sale_line.seller_discount,
            sale_line.extra_discount,
        )
        self.assertAlmostEqual(line.price_unit, expected_pu, delta=0.02)

    # --- Posted permanece imutável ---

    def test_e2e_posted_invoice_immutable_for_price_unit(self):
        """Após post, price_unit não muda mesmo com edição de seller."""
        _order, invoice, line = self._make_invoice_from_order(qty=5)
        # Forcar posted via SQL para simular sem validações fiscais
        self.env.cr.execute(
            "UPDATE account_move SET state='posted' WHERE id=%s", (invoice.id,)
        )
        invoice.invalidate_recordset()
        line.invalidate_recordset()
        snapshot_pu = line.price_unit
        # Tentar edição em posted (compute deve pular pelo gate state)
        with self.assertRaises(Exception):
            line.write({"seller_discount": 50.0})
        line.invalidate_recordset(["price_unit"])
        self.assertAlmostEqual(line.price_unit, snapshot_pu, delta=0.01)

    # --- Classificação cirúrgica de commission_rate ---

    def test_commission_rate_not_in_own_rule_kinds_global(self):
        """``commission_rate`` NÃO é classificado globalmente como own_rule.

        A flexibilização é cirúrgica: só roteado pra own_rule quando a
        divergência for coerente com o ``seller_discount`` atual da linha
        (efeito derivado de um seller edit). Tamper direto fica em
        hard_block.
        """
        AccountMove = self.env["account.move"]
        self.assertNotIn("commission_rate", AccountMove.OWN_RULE_KINDS)
        self.assertIn("seller_discount", AccountMove.OWN_RULE_KINDS)
        self.assertIn("extra_discount", AccountMove.OWN_RULE_KINDS)

    def test_commission_rate_coherent_with_seller_routed_to_own_rule(self):
        """commission_rate divergente coerente com seller atual → own_rule.

        Cenário: operador edita seller_discount no draft. Onchange recalcula
        commission_rate (no UI). Aqui simulamos o efeito do onchange
        escrevendo seller + commission_rate juntos. No post, commission_rate
        diverge do pedido — mas é coerente com o seller atual da linha. Não
        deve bloquear.

        Setup das bandas (ver common._setup_commission_bands):
          - seller até 5% → 10%
          - seller até 10% → 7%
        Pedido cria com seller=5 → commission=10. Editando seller para 7.5 →
        commission deve cair pra 7.0 (banda 5-10%).
        """
        _order, invoice, line = self._make_invoice_from_order(
            seller_discount=5.0, qty=5
        )
        # Simula efeito do onchange: seller=7.5 → banda 5-10% → commission=7.0
        line.with_context(skip_invoice_sync=True, check_move_validity=False).write(
            {"seller_discount": 7.5, "commission_rate": 7.0}
        )
        line.invalidate_recordset()
        hard_block, own_rule = invoice._split_invoice_divergence_issues()
        kinds_hard = {issue["kind"] for issue in hard_block}
        kinds_own = {issue["kind"] for issue in own_rule}
        self.assertIn("commission_rate", kinds_own)
        self.assertNotIn("commission_rate", kinds_hard)

    def test_is_commission_rate_coherent_returns_false_when_no_band(self):
        """``_is_commission_rate_coherent_with_line_seller`` retorna False
        quando não há banda aplicável para o seller atual da linha.

        Defende o caso onde o produto da linha não tem regra de comissão
        configurada (``_get_commission_rate_for_discount`` retorna False).
        """
        from unittest.mock import patch

        _order, invoice, line = self._make_invoice_from_order(qty=5)
        issue = {"kind": "commission_rate", "line_id": line.id}
        with patch.object(
            type(line), "_get_commission_rate_for_discount", return_value=False
        ):
            result = invoice._is_commission_rate_coherent_with_line_seller(issue)
        self.assertFalse(result)

    def test_commission_rate_tamper_disconnected_from_seller_hard_blocks(self):
        """commission_rate tampered fora do flow do seller → hard_block.

        Cenário: alguém escreveu commission_rate via backend/SQL com valor
        que NÃO corresponde à banda do seller atual. Deve continuar como
        hard_block — a flexibilização é só pra efeito derivado coerente.
        """
        _order, invoice, line = self._make_invoice_from_order(
            seller_discount=0.0, qty=5
        )
        # Tamper direto: commission_rate=99 desconectado do seller=0
        line.with_context(
            skip_invoice_sync=True, check_move_validity=False
        ).commission_rate = 99.0
        hard_block, own_rule = invoice._split_invoice_divergence_issues()
        kinds_hard = {issue["kind"] for issue in hard_block}
        kinds_own = {issue["kind"] for issue in own_rule}
        self.assertIn("commission_rate", kinds_hard)
        self.assertNotIn("commission_rate", kinds_own)


@tagged("post_install", "-at_install")
class TestSaleOriginRevalidationUnit(CommercialPolicyTestCommon):
    """Testes unitários cirúrgicos nos dois pontos exatos do fix.

    Os ponta-a-ponta acima validam o fluxo do operador. Estes isolam:

    1. ``_compute_price_unit`` aplica ``calc_price_unit`` em sale-origin
       quando chamado direto (sem depender do cascade write/sync).
    2. ``action_resync_from_sale_order`` escreve ``price_unit`` no payload
       — mesmo que o compute mude no futuro, o resync força o valor.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()
        cls.product_template_a.invoice_policy = "order"
        cls.product_template_b.invoice_policy = "order"

    def _make_invoice_from_order(self, seller_discount=0.0, qty=10):
        order, invoice = self._create_confirmed_order_with_invoice(
            seller_discount=seller_discount, qty=qty
        )
        line = invoice.invoice_line_ids.filtered(
            lambda sol: sol.display_type == "product"
        )[0]
        return order, invoice, line

    def test_unit_compute_price_unit_applies_calc_in_sale_origin(self):
        """``_compute_price_unit`` chamado direto recomputa via fórmula.

        Garante que o método em si não tem skip residual de sale-origin —
        mesmo se um futuro PR alterar o cascade write/sync, o compute fica
        coerente com calc_price_unit.
        """
        from ..models.policy_utils import calc_price_unit

        _order, _invoice, line = self._make_invoice_from_order(
            seller_discount=0.0, qty=5
        )
        # Edita seller/extra dentro do limite de 10% do setup
        line.with_context(check_move_validity=False).write(
            {"seller_discount": 7.5, "extra_discount": 8.0}
        )
        # Invalida cache e chama o compute diretamente
        line.invalidate_recordset(["price_unit"])
        line._compute_price_unit()
        expected = calc_price_unit(
            line.reference_price, line.seller_discount, line.extra_discount
        )
        self.assertAlmostEqual(line.price_unit, expected, delta=0.02)

    def test_unit_action_resync_writes_price_unit_field(self):
        """``action_resync_from_sale_order`` inclui price_unit no payload.

        Corrompe price_unit direto via SQL (bypass de compute/triggers) e
        verifica que o resync força o valor recalculado da sale line.
        """
        from ..models.policy_utils import calc_price_unit

        order, invoice, line = self._make_invoice_from_order(
            seller_discount=10.0, qty=5
        )
        sale_line = order.order_line[0]
        expected_pu = calc_price_unit(
            sale_line.reference_price,
            sale_line.seller_discount,
            sale_line.extra_discount,
        )
        # Corrompe price_unit por SQL pra garantir que o resync precisa
        # forçar a escrita (não basta o compute pegar valor stale do cache)
        self.env.cr.execute(
            "UPDATE account_move_line SET price_unit=%s WHERE id=%s",
            (999.99, line.id),
        )
        line.invalidate_recordset(["price_unit"])
        invoice.action_resync_from_sale_order()
        line.invalidate_recordset()
        self.assertAlmostEqual(line.price_unit, expected_pu, delta=0.02)
        self.assertNotAlmostEqual(line.price_unit, 999.99, delta=0.02)

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSupplierDiscountView(TransactionCase):
    """Garante que tr_cash_discount/tr_fob_discount são editáveis em
    fatura de fornecedor (in_invoice) draft.
    """

    def _get_invoice_form_arch(self):
        arch, _view = self.env["account.move"]._get_view(view_type="form")
        return arch

    def test_tr_cash_discount_visible_on_supplier_invoice(self):
        arch = self._get_invoice_form_arch()
        nodes = arch.xpath("//field[@name='tr_cash_discount']")
        self.assertTrue(nodes, "tr_cash_discount must be present in form arch")
        attrs = nodes[0].attrib.get("attrs", "")
        # Filtro de invisible deve incluir os 4 tipos editáveis
        self.assertIn("'in_invoice'", attrs)
        self.assertIn("'in_refund'", attrs)

    def test_tr_fob_discount_visible_on_supplier_invoice(self):
        arch = self._get_invoice_form_arch()
        nodes = arch.xpath("//field[@name='tr_fob_discount']")
        self.assertTrue(nodes, "tr_fob_discount must be present in form arch")
        attrs = nodes[0].attrib.get("attrs", "")
        self.assertIn("'in_invoice'", attrs)
        self.assertIn("'in_refund'", attrs)

    def test_invoice_discount_pct_visible_on_supplier_invoice(self):
        arch = self._get_invoice_form_arch()
        nodes = arch.xpath("//field[@name='invoice_discount_pct']")
        self.assertTrue(nodes, "invoice_discount_pct must be present")
        attrs = nodes[0].attrib.get("attrs", "")
        self.assertIn("'in_invoice'", attrs)
        self.assertIn("'in_refund'", attrs)

    def test_tr_cash_discount_editable_on_in_invoice(self):
        """Readonly só dispara em state != draft OU move_type fora dos editáveis.

        Em ``in_invoice`` draft, readonly deve ser False (campo editável).
        """
        arch = self._get_invoice_form_arch()
        nodes = arch.xpath("//field[@name='tr_cash_discount']")
        attrs = nodes[0].attrib.get("attrs", "")
        # readonly inclui 'out_invoice' E 'in_invoice' como tipos editáveis
        self.assertIn("'out_invoice', 'in_invoice'", attrs)

    def test_supplier_invoice_accepts_discount_write(self):
        """Backend aceita write — confirma que não há constraint de tipo."""
        partner = self.env["res.partner"].create({"name": "Supplier Test"})
        invoice = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "partner_id": partner.id,
            }
        )
        invoice.write({"tr_cash_discount": 3.0, "tr_fob_discount": 2.0})
        self.assertEqual(invoice.tr_cash_discount, 3.0)
        self.assertEqual(invoice.tr_fob_discount, 2.0)

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import Form, tagged

from .common import SalesRepAccessTestCommon


@tagged("post_install", "-at_install")
class TestRepMultiLineFiscal(SalesRepAccessTestCommon):
    """Reproduce the bug where, when a rep saved a sale order with new
    lines added through the Form (popup or inline), the new lines lost
    ``fiscal_operation_id`` despite the order_line widget context
    carrying ``default_fiscal_operation_id``.

    Root cause: ``_get_view`` injects ``readonly="1"`` on
    ``fiscal_operation_id`` / ``fiscal_operation_line_id`` / ``cfop_id`` /
    ``name`` for the rep so the backoffice's fiscal setup cannot be
    edited. The web client strips readonly fields from the save vals,
    discarding the default carried via context. Fix: pair the readonly
    modifier with ``force_save="1"`` so the value is sent to the server.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fo_venda = cls.env.ref("l10n_br_fiscal.fo_venda")
        # The line's ``default_fiscal_operation_id`` flows from the
        # order context, which itself defaults to
        # ``company.sale_fiscal_operation_id``. Wire it.
        cls.company.sale_fiscal_operation_id = cls.fo_venda
        # Mail-template post on order creation needs a catchall in test
        # envs without SMTP config; otherwise the Form save raises
        # ``You must either provide a sender address...``.
        ICP = cls.env["ir.config_parameter"].sudo()
        ICP.set_param("mail.catchall.domain", "test.local")
        ICP.set_param("mail.default.from", "noreply@test.local")

    def _add_line(self, order_form, product):
        with order_form.order_line.new() as line:
            line.product_id = product
            line.product_uom_qty = 1.0

    def _build_three_line_order(self, user, partner):
        SaleOrder = self.env["sale.order"].with_user(user)
        order_form = Form(SaleOrder)
        order_form.partner_id = partner
        self._add_line(order_form, self.product)
        self._add_line(order_form, self.product_allowed_sub)
        self._add_line(order_form, self.product)
        return order_form.save()

    def test_admin_multi_new_lines_baseline(self):
        """Sanity: as admin (no rep group) the same flow must populate
        ``fiscal_operation_id`` on every new line. If this fails the
        bug is upstream l10n_br_sale, not in tr_sales_rep_access.
        """
        admin = self.env.ref("base.user_admin")
        order = self._build_three_line_order(admin, self.customer_c1)
        self.assertTrue(order.fiscal_operation_id)
        missing = order.order_line.filtered(lambda line: not line.fiscal_operation_id)
        self.assertFalse(missing, "admin lines missing fiscal op: %s" % missing.ids)

    def test_rep_multi_new_lines_all_get_fiscal_operation(self):
        order = self._build_three_line_order(self.user_u1, self.customer_c1)
        self.assertTrue(
            order.fiscal_operation_id,
            "Order must carry fiscal_operation_id from company default",
        )
        missing = order.order_line.filtered(lambda line: not line.fiscal_operation_id)
        self.assertFalse(
            missing,
            "All new rep lines must inherit fiscal_operation_id from "
            "the order context default; empty on lines: %s" % missing.ids,
        )

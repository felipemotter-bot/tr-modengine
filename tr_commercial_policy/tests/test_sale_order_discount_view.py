# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import ast

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSaleOrderDiscountView(TransactionCase):
    """Guards that cash_discount and fob_discount on sale.order form
    are readonly outside ``draft``/``sent``.

    Editing these post-confirmation creates an internal drift where the
    header (cash + fob) no longer matches ``discount_rate`` / line
    discount snapshots already frozen on the order.
    """

    def _get_sale_order_form_arch(self):
        arch, _view = self.env["sale.order"]._get_view(view_type="form")
        return arch

    def _field_attrs(self, fname):
        arch = self._get_sale_order_form_arch()
        nodes = arch.xpath("//field[@name='%s']" % fname)
        self.assertTrue(nodes, "%s must be present in form arch" % fname)
        return ast.literal_eval(nodes[0].attrib.get("attrs") or "{}")

    def test_cash_discount_readonly_outside_draft_sent(self):
        attrs = self._field_attrs("cash_discount")
        self.assertEqual(
            attrs.get("readonly"),
            [("state", "not in", ["draft", "sent"])],
        )

    def test_fob_discount_readonly_outside_draft_sent(self):
        attrs = self._field_attrs("fob_discount")
        self.assertEqual(
            attrs.get("readonly"),
            [("state", "not in", ["draft", "sent"])],
        )

    def test_cash_discount_invisible_preserved(self):
        """Adding readonly must not drop the prior invisible clause."""
        attrs = self._field_attrs("cash_discount")
        self.assertEqual(
            attrs.get("invisible"),
            [("commercial_condition_id", "=", False)],
        )

    def test_fob_discount_invisible_preserved(self):
        attrs = self._field_attrs("fob_discount")
        self.assertEqual(
            attrs.get("invisible"),
            [("commercial_condition_id", "=", False)],
        )

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from lxml import etree

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import SalesRepAccessTestCommon


@tagged("post_install", "-at_install")
class TestRepPriceHistoryHide(SalesRepAccessTestCommon):
    """Coverage for PR 7 commit 2 — the
    ``sale_order_line_price_history`` widgets must be hidden from
    the rep at the view layer, with two defensive layers:

    1. Primary stop — ACL. Upstream grants
       ``sale.order.line.price.history`` read only to
       ``group_sale_salesman``, which the rep group does not imply.
       The rep cannot even open the wizard form; ``fields_view_get``
       already raises ``AccessError``.
    2. Defense in depth — view-level ``groups=``. Two widget fields
       are hidden from the rep: the "show history" widget injected
       on the sale order line tree/kanban, and the "set price from
       history" widget inside the wizard. The wizard-side hide is
       dead code today (ACL blocks first), but stays in place in
       case a downstream module opens that ACL for the rep later.
       The tree/kanban hide is the active guard: without it, rep
       would see the widget on their own orders even though every
       click would bounce off the ACL.
    """

    def _rep_sale_order_arch(self):
        view = (
            self.env["sale.order"]
            .with_user(self.user_u1)
            .fields_view_get(view_type="form")
        )
        return etree.fromstring(view["arch"])

    def _admin_price_history_wizard_arch(self):
        admin = self.env.ref("base.user_admin")
        view = (
            self.env["sale.order.line.price.history"]
            .with_user(admin)
            .fields_view_get(view_type="form")
        )
        return etree.fromstring(view["arch"])

    def test_rep_sale_order_form_hides_price_history_widget_in_tree(self):
        tree = self._rep_sale_order_arch()
        nodes = tree.xpath(
            "//field[@name='order_line']/tree//field"
            "[@widget='sale_line_price_history_widget']"
        )
        self.assertFalse(
            nodes,
            "The sale_line_price_history_widget must not appear in "
            "the rep's order line tree — the field-level groups= "
            "should filter it out",
        )

    def test_rep_sale_order_form_hides_price_history_widget_in_kanban(self):
        tree = self._rep_sale_order_arch()
        nodes = tree.xpath(
            "//field[@name='order_line']/kanban//field"
            "[@widget='sale_line_price_history_widget']"
        )
        self.assertFalse(
            nodes,
            "The sale_line_price_history_widget must not appear in "
            "the rep's order line kanban",
        )

    def test_rep_cannot_access_price_history_wizard(self):
        # Primary barrier: rep does not hold the ACL groups that
        # ``sale_order_line_price_history`` grants over
        # ``sale.order.line.price.history``. Trying to inspect the
        # wizard form already raises AccessError before the rep
        # could even see the set-price widget. The view-level hide
        # in our inherit is defense in depth for the hypothetical
        # case where a downstream module opens that ACL up; the
        # primary stop remains this one.
        with self.assertRaises(AccessError):
            self.env["sale.order.line.price.history"].with_user(
                self.user_u1
            ).fields_view_get(view_type="form")

    def test_admin_still_sees_set_price_widget_in_wizard(self):
        # Regression: the rep-only hide of the wizard widget must
        # not bleed into admin. Since admin keeps ACL on the model,
        # the view resolves normally and the widget is present.
        tree = self._admin_price_history_wizard_arch()
        nodes = tree.xpath(
            "//field[@name='target_sale_order_line_id']"
            "[@widget='set_price_to_line_widget']"
        )
        self.assertTrue(
            nodes,
            "Admin must still see the set_price_to_line_widget in "
            "the wizard — the hide is rep-only.",
        )

    def test_admin_still_sees_price_history_widget_in_order_line(self):
        # Regression: same check for the sale order form itself.
        admin = self.env.ref("base.user_admin")
        arch = etree.fromstring(
            self.env["sale.order"]
            .with_user(admin)
            .fields_view_get(view_type="form")["arch"]
        )
        self.assertTrue(
            arch.xpath(
                "//field[@name='order_line']/tree//field"
                "[@widget='sale_line_price_history_widget']"
            ),
            "Admin must still see the price history widget on the " "order line tree.",
        )

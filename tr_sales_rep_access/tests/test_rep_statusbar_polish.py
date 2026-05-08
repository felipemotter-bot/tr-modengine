# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from lxml import etree

from odoo.tests import tagged

from .common import SalesRepAccessTestCommon


@tagged("post_install", "-at_install")
class TestRepStatusbarPolish(SalesRepAccessTestCommon):
    """Statusbar polish for the rep view: hide non-rep header buttons
    and paint the tier_validation buttons green/yellow + uppercase.
    """

    def _get_form_arch(self, user):
        view = self.env["sale.order"].with_user(user).get_view(view_type="form")
        return etree.fromstring(view["arch"])

    def _modifier(self, node, key):
        import json

        mods = json.loads(node.get("modifiers") or "{}")
        return mods.get(key)

    def test_rep_hides_statusbar_buttons(self):
        arch = self._get_form_arch(self.user_u1)
        for bname in (
            "action_confirm",
            "action_cancel",
            "action_update_prices",
            "action_update_names",
        ):
            for node in arch.xpath(f"//header//button[@name='{bname}']"):
                self.assertEqual(
                    self._modifier(node, "invisible"),
                    True,
                    f"button {bname} must be invisible for rep",
                )

    def test_admin_keeps_statusbar_buttons_visible(self):
        admin = self.env.ref("base.user_admin")
        arch = self._get_form_arch(admin)
        confirm = arch.xpath("//header//button[@name='action_confirm']")
        self.assertTrue(confirm, "admin must still see Confirm")
        self.assertNotEqual(self._modifier(confirm[0], "invisible"), True)

    def test_rep_validation_buttons_painted_uppercase(self):
        arch = self._get_form_arch(self.user_u1)
        for bname, expected in (
            ("request_validation", {"btn-success", "text-uppercase"}),
            ("restart_validation", {"btn-warning", "text-uppercase"}),
        ):
            nodes = arch.xpath(f"//header//button[@name='{bname}']")
            self.assertTrue(nodes, f"rep arch must contain {bname} button")
            classes = set((nodes[0].get("class") or "").split())
            self.assertTrue(
                expected <= classes,
                f"{bname} must include {expected}, got {classes}",
            )

    def test_admin_validation_buttons_not_painted(self):
        admin = self.env.ref("base.user_admin")
        arch = self._get_form_arch(admin)
        for bname in ("request_validation", "restart_validation"):
            for node in arch.xpath(f"//header//button[@name='{bname}']"):
                classes = set((node.get("class") or "").split())
                self.assertNotIn(
                    "btn-success",
                    classes,
                    f"{bname} must not be painted for admin",
                )
                self.assertNotIn("btn-warning", classes)

    def test_get_view_non_form_short_circuits(self):
        # Tree view path: rep override must return super() result
        # untouched (no form-arch processing). Exercises the
        # ``view_type != 'form'`` branch in get_view.
        view = self.env["sale.order"].with_user(self.user_u1).get_view(view_type="tree")
        self.assertIn("arch", view)

    def test_get_view_idempotent_on_second_call(self):
        # Second call must not duplicate the css classes — covers the
        # "class already present" branch in the loop.
        sale = self.env["sale.order"].with_user(self.user_u1)
        first = sale.get_view(view_type="form")
        second = sale.get_view(view_type="form")
        first_arch = etree.fromstring(first["arch"])
        second_arch = etree.fromstring(second["arch"])
        for bname in ("request_validation", "restart_validation"):
            f_nodes = first_arch.xpath(f"//header//button[@name='{bname}']")
            s_nodes = second_arch.xpath(f"//header//button[@name='{bname}']")
            if f_nodes and s_nodes:
                self.assertEqual(
                    (f_nodes[0].get("class") or "").split().count("text-uppercase"),
                    (s_nodes[0].get("class") or "").split().count("text-uppercase"),
                    "text-uppercase must not duplicate on repeat get_view",
                )

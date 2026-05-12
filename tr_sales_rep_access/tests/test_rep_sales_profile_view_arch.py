# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from .common import SalesRepAccessTestCommon


@tagged("post_install", "-at_install")
class TestRepSalesProfileViewArch(SalesRepAccessTestCommon):
    """View arch for ``tr.sales.profile`` must load for external reps.

    Inner notebook pages on ``rule_ids`` reference ``parent.profile_type``
    in ``attrs``. Odoo's view validator requires the referenced field
    to be reachable under the user's groups; without an invisible
    mirror of ``profile_type`` available to the rep, the arch errors
    out at load time with::

        Field 'profile_type' used in attrs ... is restricted to the
        group(s) !tr_sales_rep_access.group_sales_rep_external.
    """

    def _form_arch_as_rep(self):
        return (
            self.env["tr.sales.profile"]
            .with_user(self.user_u1)
            ._get_view(view_type="form")[0]
        )

    def test_form_arch_loads_for_external_rep(self):
        """Smoke: arch resolves without raising for the rep."""
        arch = self._form_arch_as_rep()
        self.assertIsNotNone(arch)

    def test_profile_type_mirror_available_to_rep(self):
        """The invisible mirror feeds ``parent.profile_type`` attrs."""
        arch = self._form_arch_as_rep()
        nodes = arch.xpath("//field[@name='profile_type']")
        self.assertTrue(
            nodes,
            "External rep must see at least one ``profile_type`` node so"
            " ``parent.profile_type`` resolves in child attrs.",
        )
        self.assertTrue(
            all(node.attrib.get("invisible") == "1" for node in nodes),
            "External rep must only receive the invisible profile_type"
            " mirror; a visible copy would leak the field that PR #76"
            " set out to hide.",
        )

    def test_rules_tab_visible_to_rep(self):
        """Rep keeps access to the Rules notebook (their bands live there)."""
        arch = self._form_arch_as_rep()
        rules_pages = arch.xpath("//page[@name='rules']")
        self.assertTrue(rules_pages, "Rules tab must remain on the rep arch")

    def test_pricelist_ids_hidden_from_rep(self):
        """The other internal-management fields stay hidden as PR #76."""
        arch = self._form_arch_as_rep()
        nodes = arch.xpath("//field[@name='pricelist_ids']")
        self.assertFalse(
            nodes,
            "``pricelist_ids`` must remain hidden from external reps.",
        )

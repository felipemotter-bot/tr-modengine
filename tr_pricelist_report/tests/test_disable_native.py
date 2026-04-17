# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import patch

from odoo.tests.common import TransactionCase


class TestDisableNative(TransactionCase):
    def test_core_server_actions_unbound(self):
        """Native ``Print Price List`` dropdown entries are unbound."""
        for xml_id in (
            "product.action_product_price_list_report",
            "product.action_product_template_price_list_report",
        ):
            action = self.env.ref(xml_id)
            self.assertFalse(
                action.binding_model_id,
                "%s still has binding_model_id set" % xml_id,
            )
            self.assertFalse(
                action.binding_view_types,
                "%s still has binding_view_types set" % xml_id,
            )

    def test_config_parameters_created(self):
        """Config params created on install with the documented defaults."""
        icp = self.env["ir.config_parameter"].sudo()
        self.assertEqual(icp.get_param("tr_pricelist_report.validity_days"), "30")
        self.assertEqual(icp.get_param("tr_pricelist_report.category_depth"), "-2")
        self.assertEqual(
            icp.get_param("tr_pricelist_report.group_attribute_name"), "MARCA"
        )

    def test_post_init_hook_noop_when_oca_absent(self):
        """``post_init_hook`` is a no-op when the OCA module is absent."""
        from .. import hooks

        # Test DB is known not to have the OCA module installed.
        hooks.post_init_hook(self.env.cr, self.env.registry)

    def test_post_init_hook_oca_flagged_but_records_missing(self):
        """Hook handles the edge case of OCA installed but ids missing.

        Exercises the ``if action:`` / ``if menu:`` False branches — when
        every lookup in ``_OCA_BINDINGS`` returns an empty recordset and
        the menu id doesn't exist either.
        """
        from .. import hooks

        with patch.object(hooks, "_is_oca_installed", return_value=True):
            hooks.post_init_hook(self.env.cr, self.env.registry)

    def test_post_init_hook_disables_bindings_when_oca_present(self):
        """``post_init_hook`` clears bindings and hides the menu when present."""
        from .. import hooks

        # Stub OCA records with the exact external ids the hook looks up so
        # ``env.ref(...)`` resolves and the write branch is exercised.
        product_tmpl_model = self.env["ir.model"]._get("product.template")
        stub_action = self.env["ir.actions.server"].create(
            {
                "name": "Stub pricelist print",
                "model_id": product_tmpl_model.id,
                "state": "code",
                "code": "action = {}",
                "binding_model_id": product_tmpl_model.id,
                "binding_view_types": "list",
            }
        )
        self.env["ir.model.data"].create(
            {
                "module": "product_pricelist_direct_print",
                "name": "action_product_template_pricelist_print",
                "model": "ir.actions.server",
                "res_id": stub_action.id,
            }
        )
        stub_menu = self.env["ir.ui.menu"].create(
            {"name": "Stub pricelist menu", "active": True}
        )
        self.env["ir.model.data"].create(
            {
                "module": "product_pricelist_direct_print",
                "name": "menu_product_pricelist_print",
                "model": "ir.ui.menu",
                "res_id": stub_menu.id,
            }
        )
        with patch.object(hooks, "_is_oca_installed", return_value=True):
            hooks.post_init_hook(self.env.cr, self.env.registry)
        stub_action.invalidate_recordset()
        stub_menu.invalidate_recordset()
        self.assertFalse(stub_action.binding_model_id)
        self.assertFalse(stub_action.binding_view_types)
        self.assertFalse(stub_menu.active)

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import PricelistReportTestCommon


class TestMarcaDiscovery(PricelistReportTestCommon):
    """Cover ``_resolve_group_attribute`` on the post_init_hook."""

    def test_hook_resolves_existing_attribute(self):
        """When the configured name matches an attribute, id is cached."""
        from .. import hooks

        attr = self.env["product.attribute"].create(
            {"name": "DISCOVERYMARCA", "create_variant": "always"}
        )
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("tr_pricelist_report.group_attribute_name", "DISCOVERYMARCA")
        icp.set_param("tr_pricelist_report.group_attribute_id", "")
        hooks._resolve_group_attribute(self.env)
        self.assertEqual(
            icp.get_param("tr_pricelist_report.group_attribute_id"), str(attr.id)
        )

    def test_hook_leaves_id_empty_when_attribute_missing(self):
        """When no attribute matches the name, the id stays empty.

        Mode A of the Complete Pricelist layout must then treat every product
        as having no MARCA — all routed to the category fallback.
        """
        from .. import hooks

        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("tr_pricelist_report.group_attribute_name", "DOES_NOT_EXIST")
        icp.set_param("tr_pricelist_report.group_attribute_id", "999999")
        hooks._resolve_group_attribute(self.env)
        self.assertFalse(icp.get_param("tr_pricelist_report.group_attribute_id"))

    def test_wizard_reads_resolved_attribute(self):
        """``_get_group_attribute`` returns the cached attribute record."""
        attr = self.env["product.attribute"].create(
            {"name": "WIZGRP", "create_variant": "always"}
        )
        self.env["ir.config_parameter"].sudo().set_param(
            "tr_pricelist_report.group_attribute_id", str(attr.id)
        )
        wizard = self.env["tr.pricelist.report.wizard"].create(
            {"condition_id": self.condition.id, "layout": "completa"}
        )
        self.assertEqual(wizard._get_group_attribute(), attr)

    def test_wizard_get_group_attribute_empty_when_unresolved(self):
        """Empty / invalid config param returns empty recordset."""
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("tr_pricelist_report.group_attribute_id", "")
        wizard = self.env["tr.pricelist.report.wizard"].create(
            {"condition_id": self.condition.id, "layout": "completa"}
        )
        self.assertFalse(wizard._get_group_attribute())

    def test_wizard_get_group_attribute_empty_on_stale_id(self):
        """A config param pointing to a deleted id returns empty, not crash."""
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("tr_pricelist_report.group_attribute_id", "999999")
        wizard = self.env["tr.pricelist.report.wizard"].create(
            {"condition_id": self.condition.id, "layout": "completa"}
        )
        self.assertFalse(wizard._get_group_attribute())

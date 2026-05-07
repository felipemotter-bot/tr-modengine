# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import PricelistReportTestCommon


class TestConfigSettings(PricelistReportTestCommon):
    """Cover the res.config.settings extension for pricelist report params."""

    def _get_param(self, key):
        return self.env["ir.config_parameter"].sudo().get_param(key)

    def test_round_trip_category_depth(self):
        """Settings round-trips category_depth to/from ir.config_parameter."""
        settings = self.env["res.config.settings"].create(
            {"tr_pricelist_report_category_depth": -2}
        )
        settings.execute()
        self.assertEqual(self._get_param("tr_pricelist_report.category_depth"), "-2")

    def test_round_trip_group_attribute_name(self):
        """Settings round-trips group_attribute_name."""
        settings = self.env["res.config.settings"].create(
            {"tr_pricelist_report_group_attribute_name": "BRAND"}
        )
        settings.execute()
        self.assertEqual(
            self._get_param("tr_pricelist_report.group_attribute_name"),
            "BRAND",
        )

    def test_round_trip_category_depth_zero(self):
        """Zero boundary: category_depth=0 saves and reads back literally.

        Covers the workaround that bypasses Odoo's 0-to-empty quirk for
        this specific field. Without the workaround, saving 0 would
        collapse to '' and then the wizard's ``_get_category_depth``
        int-cast would crash.
        """
        settings = self.env["res.config.settings"].create(
            {"tr_pricelist_report_category_depth": 0}
        )
        settings.execute()
        self.assertEqual(self._get_param("tr_pricelist_report.category_depth"), "0")

    def test_round_trip_history_months_back_zero(self):
        """Zero boundary: history_months_back=0 saves and reads back."""
        settings = self.env["res.config.settings"].create(
            {"tr_pricelist_report_history_months_back": 0}
        )
        settings.execute()
        self.assertEqual(
            self._get_param("tr_pricelist_report.history_months_back"), "0"
        )

    def test_round_trip_invalid_price_threshold(self):
        """Settings round-trips invalid_price_threshold."""
        settings = self.env["res.config.settings"].create(
            {"tr_pricelist_report_invalid_price_threshold": 50000.0}
        )
        settings.execute()
        self.assertEqual(
            self._get_param("tr_pricelist_report.invalid_price_threshold"),
            "50000.0",
        )

    def test_set_values_recomputes_group_attribute_cache(self):
        """Saving settings always re-resolves group_attribute_id.

        Even when the user didn't touch the name — the call is unconditional
        by design. This test covers the most load-bearing case: user types a
        new name pointing at a different attribute.
        """
        attribute = self.env["product.attribute"].create(
            {"name": "MYBRAND", "create_variant": "always"}
        )
        settings = self.env["res.config.settings"].create(
            {"tr_pricelist_report_group_attribute_name": "MYBRAND"}
        )
        settings.execute()
        self.assertEqual(
            self._get_param("tr_pricelist_report.group_attribute_id"),
            str(attribute.id),
        )

    def test_set_values_clears_cache_when_name_misses(self):
        """Name that matches no attribute clears the cached id."""
        settings = self.env["res.config.settings"].create(
            {"tr_pricelist_report_group_attribute_name": "DOES_NOT_EXIST"}
        )
        settings.execute()
        self.assertFalse(self._get_param("tr_pricelist_report.group_attribute_id"))

    # ------------------------------------------------------------------
    # get_values defaults (no XML seed records anymore)
    # ------------------------------------------------------------------

    def _drop_param(self, key):
        param = (
            self.env["ir.config_parameter"].sudo().search([("key", "=", key)], limit=1)
        )
        if param:
            param.unlink()

    def test_get_values_returns_defaults_when_params_missing(self):
        """Missing params surface as the in-code defaults via ``get_values``.

        Since the seed XML was removed, fresh installs never have these
        rows in ``ir.config_parameter`` until the user opens Settings. The
        wizard form must still display the documented defaults.
        """
        icp = self.env["ir.config_parameter"].sudo()
        # Pre-populate so ``_drop_param`` exercises the unlink branch
        # regardless of fixture/test ordering.
        icp.set_param("tr_pricelist_report.category_depth", "5")
        icp.set_param("tr_pricelist_report.history_months_back", "5")
        icp.set_param("tr_pricelist_report.invalid_price_threshold", "5.0")
        icp.set_param("tr_pricelist_report.group_attribute_name", "FOO")
        for key in (
            "tr_pricelist_report.category_depth",
            "tr_pricelist_report.history_months_back",
            "tr_pricelist_report.invalid_price_threshold",
            "tr_pricelist_report.group_attribute_name",
        ):
            self._drop_param(key)
        values = self.env["res.config.settings"].default_get(
            [
                "tr_pricelist_report_category_depth",
                "tr_pricelist_report_history_months_back",
                "tr_pricelist_report_invalid_price_threshold",
                "tr_pricelist_report_group_attribute_name",
            ]
        )
        # default_get + get_values composes; final values are the defaults.
        settings = self.env["res.config.settings"].create(values)
        self.assertEqual(settings.tr_pricelist_report_category_depth, -2)
        self.assertEqual(settings.tr_pricelist_report_history_months_back, 6)
        self.assertEqual(settings.tr_pricelist_report_invalid_price_threshold, 99999.0)
        self.assertEqual(settings.tr_pricelist_report_group_attribute_name, "MARCA")

    def test_get_values_handles_empty_param_as_default(self):
        """Param explicitly set to '' (empty string) reads back as default.

        Edge case: ``ir.config_parameter.set_param(key, "")`` would normally
        unlink the row, but if a row with empty value reaches the cast
        path, ``int("")`` would crash. ``get_values`` defends against that
        by falling back to the documented default.
        """
        icp = self.env["ir.config_parameter"].sudo()
        # Force-write an empty string by going around set_param's unlink.
        param = icp.search(
            [("key", "=", "tr_pricelist_report.category_depth")], limit=1
        )
        if not param:
            param = icp.create(
                {"key": "tr_pricelist_report.category_depth", "value": "-2"}
            )
        param.value = ""
        settings = self.env["res.config.settings"].create({})
        self.assertEqual(settings.tr_pricelist_report_category_depth, -2)

    # ------------------------------------------------------------------
    # ``set_param`` falsy quirk regression
    # ------------------------------------------------------------------

    def test_set_values_with_zero_does_not_destroy_param_record(self):
        """Saving 0 must not unlink the ``ir.config_parameter`` row.

        Without the fix (``config_parameter=`` declared on the field),
        ``super().set_values()`` collapses 0 to '' and ``set_param('')``
        unlinks the row — losing any ``ir.model.data`` xml_id seed
        attached to it. Pre-create the param BEFORE the first save to
        prove the row survives the first 0 save itself, not just
        subsequent saves.
        """
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("tr_pricelist_report.category_depth", "-2")
        original_id = icp.search(
            [("key", "=", "tr_pricelist_report.category_depth")], limit=1
        ).id

        settings = self.env["res.config.settings"].create(
            {"tr_pricelist_report_category_depth": 0}
        )
        settings.execute()

        param = icp.search(
            [("key", "=", "tr_pricelist_report.category_depth")], limit=1
        )
        self.assertEqual(
            param.id,
            original_id,
            "Saving 0 unlinked the param and recreated it (lost the row id).",
        )
        self.assertEqual(param.value, "0")

    def test_set_values_with_zero_preserves_xml_id_link(self):
        """Saving 0 keeps any ``ir.model.data`` link to the param intact.

        Direct test of the bug we hit in production: with the old wiring,
        saving 0 would unlink the param, the linked ``ir.model.data``
        would lose its target, and ``-u tr_pricelist_report`` later would
        try to recreate the row from the seed XML and trip the unique
        constraint on ``ir_config_parameter.key``.
        """
        icp = self.env["ir.config_parameter"].sudo()
        imd = self.env["ir.model.data"].sudo()
        icp.set_param("tr_pricelist_report.category_depth", "-2")
        param = icp.search(
            [("key", "=", "tr_pricelist_report.category_depth")], limit=1
        )
        # Stub a fake xml_id pointing at the param to reproduce the
        # production-state link.
        seed = imd.create(
            {
                "module": "tr_pricelist_report",
                "name": "test_seed_category_depth",
                "model": "ir.config_parameter",
                "res_id": param.id,
                "noupdate": True,
            }
        )

        settings = self.env["res.config.settings"].create(
            {"tr_pricelist_report_category_depth": 0}
        )
        settings.execute()

        # Param row id stays the same.
        param_after = icp.search(
            [("key", "=", "tr_pricelist_report.category_depth")], limit=1
        )
        self.assertEqual(param_after.id, param.id)
        # ir.model.data still resolves to the same row (no orphan link).
        seed.invalidate_recordset()
        self.assertEqual(seed.res_id, param.id)

    def test_get_values_handles_garbage_int_param(self):
        """Non-numeric param value falls back to default, doesn't crash.

        Without the ``try/except`` in ``_get_int_param``, opening Settings
        with ``"not_a_number"`` stored under a numeric key would propagate
        a ``ValueError`` and brick the Settings page. The reader uses the
        documented default instead.
        """
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("tr_pricelist_report.category_depth", "not_a_number")
        icp.set_param("tr_pricelist_report.history_months_back", "garbage")
        icp.set_param("tr_pricelist_report.invalid_price_threshold", "n/a")
        settings = self.env["res.config.settings"].create({})
        self.assertEqual(settings.tr_pricelist_report_category_depth, -2)
        self.assertEqual(settings.tr_pricelist_report_history_months_back, 6)
        self.assertEqual(settings.tr_pricelist_report_invalid_price_threshold, 99999.0)

    def test_set_values_with_empty_group_attribute_falls_back_to_default(self):
        """Empty Char saves as 'MARCA' to avoid the falsy unlink path."""
        settings = self.env["res.config.settings"].create(
            {"tr_pricelist_report_group_attribute_name": ""}
        )
        settings.execute()
        self.assertEqual(
            self._get_param("tr_pricelist_report.group_attribute_name"), "MARCA"
        )

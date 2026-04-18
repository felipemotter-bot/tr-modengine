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

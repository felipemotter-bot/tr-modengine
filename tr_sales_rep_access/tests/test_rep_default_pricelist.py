# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import SalesRepAccessTestCommon

DEFAULT_PRICELIST_PARAM = "tr_sales_rep_access.tr_sales_rep_default_pricelist_ids"


class TestDefaultPricelistICP(SalesRepAccessTestCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pricelist_default_a = cls.env["product.pricelist"].create(
            {
                "name": "Default Rep Pricelist A",
                "currency_id": cls.env.ref("base.BRL").id,
            }
        )
        cls.pricelist_default_b = cls.env["product.pricelist"].create(
            {
                "name": "Default Rep Pricelist B",
                "currency_id": cls.env.ref("base.BRL").id,
            }
        )
        cls.env["ir.config_parameter"].sudo().set_param(
            DEFAULT_PRICELIST_PARAM,
            f"{cls.pricelist_default_a.id},{cls.pricelist_default_b.id}",
        )

    def _make_profile(self, name):
        return self.env["tr.sales.profile"].create(
            {
                "name": name,
                "profile_type": "agent",
                "pricelist_ids": [(6, 0, [self.pricelist.id])],
                "cash_discount_max": 0.0,
                "fob_discount_max": 0.0,
                "cash_term_avg_days_max": 30,
                "manager_extra_limit": 0.0,
                "rule_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "general",
                            "sequence": 999,
                            "commission_band_ids": [
                                (
                                    0,
                                    0,
                                    {"discount_up_to": 100.0, "commission_rate": 0.0},
                                )
                            ],
                        },
                    ),
                ],
            }
        )

    def test_create_agent_auto_populates_pricelist(self):
        """agent=True + ICP set + no explicit vals → auto-populate."""
        profile = self._make_profile("Agent Auto PL Populate")
        agent = self.env["res.partner"].create(
            {
                "name": "Agent Auto Populate",
                "agent": True,
                "sales_profile_id": profile.id,
                "commission_id": self.commission.id,
            }
        )
        self.assertEqual(
            set(agent.allowed_pricelist_ids.ids),
            {self.pricelist_default_a.id, self.pricelist_default_b.id},
        )

    def test_create_agent_without_icp_leaves_empty(self):
        """Empty ICP → agent created with empty whitelist."""
        self.env["ir.config_parameter"].sudo().set_param(DEFAULT_PRICELIST_PARAM, "")
        profile = self._make_profile("Agent Empty ICP")
        agent = self.env["res.partner"].create(
            {
                "name": "Agent Empty ICP",
                "agent": True,
                "sales_profile_id": profile.id,
                "commission_id": self.commission.id,
            }
        )
        self.assertFalse(agent.allowed_pricelist_ids)

    def test_create_non_agent_does_not_populate(self):
        """Non-agent partner is not touched by the auto-populate."""
        partner = self.env["res.partner"].create({"name": "Non Agent", "agent": False})
        self.assertFalse(partner.allowed_pricelist_ids)

    def test_create_with_explicit_pricelist_respects_it(self):
        """Explicit allowed_pricelist_ids in vals is not overwritten."""
        profile = self._make_profile("Agent Explicit PL")
        agent = self.env["res.partner"].create(
            {
                "name": "Agent Explicit PL",
                "agent": True,
                "sales_profile_id": profile.id,
                "commission_id": self.commission.id,
                "allowed_pricelist_ids": [(6, 0, [])],
            }
        )
        self.assertFalse(agent.allowed_pricelist_ids)

    def test_create_agent_with_invalid_icp_leaves_empty(self):
        """Garbled CSV in the ICP → helper returns []; agent stays empty."""
        self.env["ir.config_parameter"].sudo().set_param(
            DEFAULT_PRICELIST_PARAM, "not-a-csv-of-ints"
        )
        profile = self._make_profile("Agent Invalid ICP")
        agent = self.env["res.partner"].create(
            {
                "name": "Agent Invalid ICP",
                "agent": True,
                "sales_profile_id": profile.id,
                "commission_id": self.commission.id,
            }
        )
        self.assertFalse(agent.allowed_pricelist_ids)

    def test_config_settings_roundtrip(self):
        """execute() persists the field into the ICP; get_values reads it back."""
        self.env["ir.config_parameter"].sudo().set_param(DEFAULT_PRICELIST_PARAM, "")
        Settings = self.env["res.config.settings"]
        settings = Settings.create(
            {
                "tr_sales_rep_default_pricelist_ids": [
                    (6, 0, [self.pricelist_default_a.id, self.pricelist_default_b.id])
                ],
            }
        )
        settings.execute()
        read_settings = Settings.create({})
        self.assertIn(
            self.pricelist_default_a,
            read_settings.tr_sales_rep_default_pricelist_ids,
        )
        self.assertIn(
            self.pricelist_default_b,
            read_settings.tr_sales_rep_default_pricelist_ids,
        )

    def test_config_settings_empty_roundtrip(self):
        """Setting the field to empty clears the stored param."""
        self.env["ir.config_parameter"].sudo().set_param(
            DEFAULT_PRICELIST_PARAM, str(self.pricelist_default_a.id)
        )
        settings = self.env["res.config.settings"].create(
            {"tr_sales_rep_default_pricelist_ids": [(6, 0, [])]}
        )
        settings.execute()
        stored = (
            self.env["ir.config_parameter"].sudo().get_param(DEFAULT_PRICELIST_PARAM)
        )
        self.assertFalse(stored)

    def test_config_settings_get_values_with_garbled_param(self):
        """get_values() drops a non-integer param without raising."""
        self.env["ir.config_parameter"].sudo().set_param(
            DEFAULT_PRICELIST_PARAM, "foo,bar,baz"
        )
        settings = self.env["res.config.settings"].create({})
        self.assertFalse(settings.tr_sales_rep_default_pricelist_ids)

    def test_config_settings_ignores_stale_ids(self):
        """Ids pointing to deleted pricelists are silently dropped."""
        stale = self.env["product.pricelist"].create(
            {
                "name": "Stale Pricelist",
                "currency_id": self.env.ref("base.BRL").id,
            }
        )
        self.env["ir.config_parameter"].sudo().set_param(
            DEFAULT_PRICELIST_PARAM,
            f"{self.pricelist_default_a.id},{stale.id}",
        )
        stale.unlink()
        settings = self.env["res.config.settings"].create({})
        self.assertEqual(
            settings.tr_sales_rep_default_pricelist_ids,
            self.pricelist_default_a,
        )

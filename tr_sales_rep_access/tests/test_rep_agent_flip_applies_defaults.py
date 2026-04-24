# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import SalesRepAccessTestCommon

DEFAULT_CATALOG_PARAM = "tr_sales_rep_access.tr_sales_rep_default_category_ids"
DEFAULT_PRICELIST_PARAM = "tr_sales_rep_access.tr_sales_rep_default_pricelist_ids"


class TestAgentFlipAppliesDefaults(SalesRepAccessTestCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pricelist_default = cls.env["product.pricelist"].create(
            {
                "name": "Flip Default Pricelist",
                "currency_id": cls.env.ref("base.BRL").id,
            }
        )
        ICP = cls.env["ir.config_parameter"].sudo()
        ICP.set_param(DEFAULT_CATALOG_PARAM, str(cls.cat_allowed.id))
        ICP.set_param(DEFAULT_PRICELIST_PARAM, str(cls.pricelist_default.id))
        cls.commercial_profile = cls.env["tr.sales.profile"].create(
            {
                "name": "Flip Profile",
                "profile_type": "agent",
                "pricelist_ids": [(6, 0, [cls.pricelist.id])],
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

        # Internal user with res.partner write access but **not**
        # the sales manager group. Proves that the ``sudo()`` in
        # ``_sales_rep_apply_defaults_on_agent_flip`` bypasses the
        # manager-only ``groups=`` on the whitelist fields even when
        # the acting user cannot read/write them directly. Uses
        # ``base.group_partner_manager`` (Contact Creation) — the
        # minimal realistic group that grants res.partner write ACL
        # without the sales manager bundle.
        cls.internal_user = cls.env["res.users"].create(
            {
                "name": "Internal Flipper",
                "login": "tsra_internal_flipper",
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            cls.env.ref("base.group_user").id,
                            cls.env.ref("base.group_partner_manager").id,
                        ],
                    )
                ],
            }
        )

    def _make_non_agent_partner(self, name="Will become agent"):
        return self.env["res.partner"].create({"name": name, "agent": False})

    def _flip_to_agent(self, partner, extra_vals=None):
        vals = {
            "agent": True,
            "sales_profile_id": self.commercial_profile.id,
            "commission_id": self.commission.id,
        }
        if extra_vals:
            vals.update(extra_vals)
        partner.write(vals)

    def test_write_flip_populates_empty_pricelist(self):
        partner = self._make_non_agent_partner("Flip PL Empty")
        self.assertFalse(partner.allowed_pricelist_ids)
        self._flip_to_agent(partner)
        self.assertIn(self.pricelist_default, partner.allowed_pricelist_ids)

    def test_write_flip_populates_empty_category(self):
        """Fix for the pre-existing bug in the category default flow."""
        partner = self._make_non_agent_partner("Flip Cat Empty")
        self.assertFalse(partner.allowed_category_ids)
        self._flip_to_agent(partner)
        self.assertIn(self.cat_allowed, partner.allowed_category_ids)

    def test_write_flip_does_not_overwrite_existing_pricelist(self):
        other_pl = self.env["product.pricelist"].create(
            {"name": "Manual PL", "currency_id": self.env.ref("base.BRL").id}
        )
        partner = self._make_non_agent_partner("Flip PL Manual")
        partner.sudo().write({"allowed_pricelist_ids": [(6, 0, [other_pl.id])]})
        self._flip_to_agent(partner)
        self.assertEqual(partner.allowed_pricelist_ids, other_pl)

    def test_write_flip_does_not_overwrite_existing_category(self):
        partner = self._make_non_agent_partner("Flip Cat Manual")
        partner.sudo().write({"allowed_category_ids": [(6, 0, [self.cat_other.id])]})
        self._flip_to_agent(partner)
        self.assertEqual(partner.allowed_category_ids, self.cat_other)

    def test_write_without_agent_change_no_op(self):
        partner = self._make_non_agent_partner("Flip No Op")
        partner.write({"name": "Flip No Op 2"})
        self.assertFalse(partner.allowed_pricelist_ids)
        self.assertFalse(partner.allowed_category_ids)

    def test_write_agent_true_to_true_no_op(self):
        partner = self.env["res.partner"].create(
            {
                "name": "Already Agent",
                "agent": True,
                "sales_profile_id": self.commercial_profile.id,
                "commission_id": self.commission.id,
                "allowed_pricelist_ids": [(6, 0, [])],
                "allowed_category_ids": [(6, 0, [])],
            }
        )
        partner.write({"agent": True})
        self.assertFalse(partner.allowed_pricelist_ids)
        self.assertFalse(partner.allowed_category_ids)

    def test_write_flip_with_explicit_pricelist_respects_it(self):
        partner = self._make_non_agent_partner("Flip PL Explicit")
        self._flip_to_agent(partner, extra_vals={"allowed_pricelist_ids": [(6, 0, [])]})
        self.assertFalse(partner.allowed_pricelist_ids)

    def test_write_flip_with_explicit_category_respects_it(self):
        partner = self._make_non_agent_partner("Flip Cat Explicit")
        self._flip_to_agent(partner, extra_vals={"allowed_category_ids": [(6, 0, [])]})
        self.assertFalse(partner.allowed_category_ids)

    def test_write_flip_as_internal_non_manager_applies_defaults(self):
        """Helper uses sudo() to apply defaults even when the acting
        user has no access to the manager-only whitelist fields."""
        partner = self._make_non_agent_partner("Flip Internal User")
        partner.with_user(self.internal_user).write({"agent": True})
        self.assertIn(self.pricelist_default, partner.allowed_pricelist_ids)
        self.assertIn(self.cat_allowed, partner.allowed_category_ids)

    def test_write_flip_with_both_whitelists_explicit_no_op(self):
        """Helper short-circuits when vals carries both whitelist fields."""
        partner = self._make_non_agent_partner("Flip Both Explicit")
        self._flip_to_agent(
            partner,
            extra_vals={
                "allowed_category_ids": [(6, 0, [])],
                "allowed_pricelist_ids": [(6, 0, [])],
            },
        )
        self.assertFalse(partner.allowed_category_ids)
        self.assertFalse(partner.allowed_pricelist_ids)

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import SalesRepAccessTestCommon


@tagged("post_install", "-at_install")
class TestRepSalesProfileVisibility(SalesRepAccessTestCommon):
    """Record rule isolating tr.sales.profile (and its children) to
    the rep's own profile on the active companies.

    ``partner.sales_profile_id`` is ``company_dependent``, so the
    domain iterates over ``company_ids`` (allowed_company_ids) to
    pick up every profile the rep owns on currently enabled
    companies.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager_user = cls.env["res.users"].create(
            {
                "name": "Profile Visibility Manager",
                "login": "tsra_profile_vis_manager",
                "groups_id": [
                    (4, cls.env.ref("base.group_user").id),
                    (
                        4,
                        cls.env.ref("tr_commercial_policy.group_sales_manager").id,
                    ),
                ],
            }
        )
        cls.director_user = cls.env["res.users"].create(
            {
                "name": "Profile Visibility Director",
                "login": "tsra_profile_vis_director",
                "groups_id": [
                    (4, cls.env.ref("base.group_user").id),
                    (
                        4,
                        cls.env.ref("tr_commercial_policy.group_sales_director").id,
                    ),
                ],
            }
        )

    # ------------------------------------------------------------------
    # Single-company isolation
    # ------------------------------------------------------------------

    def test_rep_sees_only_own_profile_single_company(self):
        profiles = self.env["tr.sales.profile"].with_user(self.user_u1).search([])
        self.assertIn(self.profile_a1, profiles)
        self.assertNotIn(self.profile_a2, profiles)

    def test_rep_cannot_browse_read_other_profile(self):
        with self.assertRaises(AccessError):
            self.profile_a2.with_user(self.user_u1).read(["name"])

    def test_rep_cannot_search_rule_of_other_profile(self):
        other_rule = self.profile_a2.rule_ids[:1]
        self.assertTrue(other_rule, "Setup: profile_a2 should have a rule")
        found = (
            self.env["tr.sales.profile.rule"]
            .with_user(self.user_u1)
            .search([("id", "=", other_rule.id)])
        )
        self.assertFalse(found)

    def test_rep_cannot_browse_read_rule_of_other_profile(self):
        other_rule = self.profile_a2.rule_ids[:1]
        self.assertTrue(other_rule)
        with self.assertRaises(AccessError):
            other_rule.with_user(self.user_u1).read(["profile_id"])

    def test_rep_cannot_search_commission_band_of_other_profile(self):
        other_band = self.profile_a2.rule_ids.commission_band_ids[:1]
        self.assertTrue(other_band)
        found = (
            self.env["tr.sales.profile.commission.band"]
            .with_user(self.user_u1)
            .search([("id", "=", other_band.id)])
        )
        self.assertFalse(found)

    def test_rep_cannot_browse_read_commission_band_of_other_profile(self):
        other_band = self.profile_a2.rule_ids.commission_band_ids[:1]
        self.assertTrue(other_band)
        with self.assertRaises(AccessError):
            other_band.with_user(self.user_u1).read(["commission_rate"])

    def _make_foreign_internal_profile(self):
        """Profile with ``profile_type=internal`` so the constrain
        demanding order_value_band_ids is satisfied and the order.band
        rule can be exercised on a foreign record."""
        return self.env["tr.sales.profile"].create(
            {
                "name": "Foreign Internal Profile",
                "profile_type": "internal",
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
                            "order_value_band_ids": [
                                (
                                    0,
                                    0,
                                    {
                                        "order_min_amount": 0.0,
                                        "seller_discount_max": 5.0,
                                    },
                                ),
                            ],
                        },
                    ),
                ],
            }
        )

    def test_rep_cannot_search_order_band_of_other_profile(self):
        other_band = (
            self._make_foreign_internal_profile().rule_ids.order_value_band_ids[:1]
        )
        self.assertTrue(other_band)
        found = (
            self.env["tr.sales.profile.order.band"]
            .with_user(self.user_u1)
            .search([("id", "=", other_band.id)])
        )
        self.assertFalse(found)

    def test_rep_cannot_browse_read_order_band_of_other_profile(self):
        other_band = (
            self._make_foreign_internal_profile().rule_ids.order_value_band_ids[:1]
        )
        self.assertTrue(other_band)
        with self.assertRaises(AccessError):
            other_band.with_user(self.user_u1).read(["seller_discount_max"])

    def test_rep_can_read_own_profile_children(self):
        profile = self.profile_a1.with_user(self.user_u1)
        self.assertTrue(profile.rule_ids)
        self.assertTrue(profile.rule_ids.commission_band_ids)
        # Touching the related fields triggers ACL/rule checks.
        profile.rule_ids.read(["profile_id"])
        profile.rule_ids.commission_band_ids.read(["commission_rate"])

    # ------------------------------------------------------------------
    # Without a profile
    # ------------------------------------------------------------------

    def test_rep_without_profile_sees_nothing(self):
        self.agent_a1.sales_profile_id = False
        profiles = self.env["tr.sales.profile"].with_user(self.user_u1).search([])
        self.assertFalse(profiles)

    # ------------------------------------------------------------------
    # Manager / Director bypass the rule
    # ------------------------------------------------------------------

    def test_manager_sees_all_profiles(self):
        profiles = self.env["tr.sales.profile"].with_user(self.manager_user).search([])
        self.assertIn(self.profile_a1, profiles)
        self.assertIn(self.profile_a2, profiles)

    def test_director_sees_all_profiles(self):
        profiles = self.env["tr.sales.profile"].with_user(self.director_user).search([])
        self.assertIn(self.profile_a1, profiles)
        self.assertIn(self.profile_a2, profiles)

    # ------------------------------------------------------------------
    # Multi-company
    # ------------------------------------------------------------------

    def _setup_multi_company(self):
        """Spin up a second company with a profile for U1."""
        company_b = self.env["res.company"].create({"name": "Company B"})
        self.user_u1.write({"company_ids": [(4, company_b.id)]})
        profile_b = (
            self.env["tr.sales.profile"]
            .with_company(company_b)
            .create(
                {
                    "name": "U1 Profile on B",
                    "profile_type": "agent",
                    "pricelist_ids": [(6, 0, [self.pricelist.id])],
                    "cash_discount_max": 0.0,
                    "fob_discount_max": 0.0,
                    "cash_term_avg_days_max": 30,
                    "manager_extra_limit": 0.0,
                    "company_id": company_b.id,
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
                                        {
                                            "discount_up_to": 100.0,
                                            "commission_rate": 0.0,
                                        },
                                    )
                                ],
                            },
                        ),
                    ],
                }
            )
        )
        # Assign the company-dependent field with the right company
        # context so the value lands on company B, not on the main one.
        self.agent_a1.with_company(company_b).sales_profile_id = profile_b
        return company_b, profile_b

    def test_rep_multi_company_sees_both_profiles_when_allowed(self):
        company_b, profile_b = self._setup_multi_company()
        profiles = (
            self.env["tr.sales.profile"]
            .with_user(self.user_u1)
            .with_company(self.company)
            .with_context(allowed_company_ids=[self.company.id, company_b.id])
            .search([])
        )
        self.assertIn(self.profile_a1, profiles)
        self.assertIn(profile_b, profiles)
        self.assertNotIn(self.profile_a2, profiles)

    def test_rep_multi_company_sees_only_allowed_profile(self):
        company_b, profile_b = self._setup_multi_company()
        profiles = (
            self.env["tr.sales.profile"]
            .with_user(self.user_u1)
            .with_company(self.company)
            .with_context(allowed_company_ids=[self.company.id])
            .search([])
        )
        self.assertIn(self.profile_a1, profiles)
        self.assertNotIn(profile_b, profiles)

    # ------------------------------------------------------------------
    # sale.order.line computes must still resolve under the rule
    # ------------------------------------------------------------------

    def test_sale_order_line_computes_without_access_error_as_rep(self):
        order = self._make_order(self.customer_c1)
        line = order.order_line[:1]
        line_as_rep = line.with_user(self.user_u1)
        # Both computes traverse order_id.sales_profile_id.rule_ids and
        # rule_ids.commission_band_ids, which would now be blocked by
        # the rules if the rep's own profile couldn't resolve.
        line_as_rep.read(["seller_discount_max", "commission_rate"])

    def test_rep_multi_company_profile_missing_in_one_does_not_crash(self):
        # Second company exists and is allowed, but U1 has no profile
        # on it (company-dependent field left empty). Search should
        # still work and return only the companies where there is one.
        company_b = self.env["res.company"].create({"name": "Company B Empty"})
        self.user_u1.write({"company_ids": [(4, company_b.id)]})
        profiles = (
            self.env["tr.sales.profile"]
            .with_user(self.user_u1)
            .with_company(self.company)
            .with_context(allowed_company_ids=[self.company.id, company_b.id])
            .search([])
        )
        self.assertIn(self.profile_a1, profiles)

    def test_rep_can_read_profile_when_active_company_lacks_one(self):
        """Regression: rep with profile in company A but switched to
        company B (allowed_company_ids = [B] only) tries to read a
        sale.order whose ``applicable_profile_id`` resolves to the
        profile in company A. The record rule must not produce a
        ``[False]`` list in SQL (which becomes ``IN (NULL)`` and
        silently denies access). Once filtered for falsy values, the
        list is empty and the rule fails closed — no AccessError leaks
        from a stray NULL.

        Captures the bug observed in devel: rep PM with allowed
        companies [TRENTO, TREINAMENTO] but only profile in TRENTO,
        editing a sale.order in TREINAMENTO whose condition pointed at
        the TRENTO profile, silently failed read with confusing message.
        """
        company_b = self.env["res.company"].create({"name": "Company B (no profile)"})
        self.user_u1.write({"company_ids": [(4, company_b.id)]})
        # Switch to company B only — profile_a1 is in company A.
        profiles = (
            self.env["tr.sales.profile"]
            .with_user(self.user_u1)
            .with_company(company_b)
            .with_context(allowed_company_ids=[company_b.id])
            .search([])
        )
        # Rep has no profile in company B → fail-closed: empty result
        # set, NOT an AccessError. The previous ``[False]`` ⇒ ``IN (NULL)``
        # behavior would have silently let SQL match nothing while
        # making downstream reads on profile records raise.
        self.assertFalse(profiles)
        # And direct read of a profile from another company still raises
        # AccessError (intended), not the silent NULL trap.
        with self.assertRaises(AccessError):
            self.profile_a1.with_user(self.user_u1).with_company(company_b).read(
                ["name"]
            )

    def test_rep_can_read_profile_with_mixed_companies_one_empty(self):
        """Regression: lista da record rule pode misturar profile.id
        válido com False (company sem profile) — resultado deve casar
        o id válido sem ser confundido por entradas falsy.
        """
        company_b = self.env["res.company"].create({"name": "Company B (no profile)"})
        self.user_u1.write({"company_ids": [(4, company_b.id)]})
        # Both companies allowed; profile only in A.
        profile_records = (
            self.env["tr.sales.profile"]
            .with_user(self.user_u1)
            .with_company(self.company)
            .with_context(allowed_company_ids=[self.company.id, company_b.id])
            .search([])
        )
        self.assertIn(self.profile_a1, profile_records)
        # Read attribute too — the underlying rule check runs again here.
        self.profile_a1.with_user(self.user_u1).with_context(
            allowed_company_ids=[self.company.id, company_b.id]
        ).read(["name"])

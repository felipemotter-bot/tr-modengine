# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestProfileResolutionWithCompany(CommercialPolicyTestCommon):
    """Multi-company resolution of ``applicable_profile_id`` on
    ``partner.commercial.condition``.

    Regression: the resolver used to read ``sales_profile_id``
    (``company_dependent``) without ``with_company``, so the stored
    compute could end up pointing at a profile of the wrong company.
    These tests lock the new behavior: each branch of the resolution
    chain must produce a profile that belongs to the condition's
    company, or fall through to the next branch.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env.ref("base.main_company")
        cls.company_b = cls.env["res.company"].create(
            {"name": "Profile Resolution Test B"}
        )
        cls.env.user.company_ids = [(4, cls.company_b.id)]

        # Pricelists per company so check_company on condition.pricelist
        # is satisfied.
        cls.pricelist_a = cls.env["product.pricelist"].create(
            {"name": "PL A", "company_id": cls.company_a.id}
        )
        cls.pricelist_b = cls.env["product.pricelist"].create(
            {"name": "PL B", "company_id": cls.company_b.id}
        )

        cls.profile_a = cls.env["tr.sales.profile"].create(
            {
                "name": "Profile A",
                "profile_type": "agent",
                "company_id": cls.company_a.id,
                "pricelist_ids": [(6, 0, [cls.pricelist_a.id])],
                "cash_discount_max": 3.0,
                "fob_discount_max": 3.0,
                "cash_term_avg_days_max": 30,
                "manager_extra_limit": 0.0,
                "rule_ids": cls._placeholder_rule_vals("agent"),
            }
        )
        cls.profile_b = (
            cls.env["tr.sales.profile"]
            .with_company(cls.company_b)
            .create(
                {
                    "name": "Profile B",
                    "profile_type": "agent",
                    "company_id": cls.company_b.id,
                    "pricelist_ids": [(6, 0, [cls.pricelist_b.id])],
                    "cash_discount_max": 5.0,
                    "fob_discount_max": 5.0,
                    "cash_term_avg_days_max": 30,
                    "manager_extra_limit": 0.0,
                    "rule_ids": cls._placeholder_rule_vals("agent"),
                }
            )
        )

        # Agent partner with profile only in company A. Each test
        # explicitly toggles the company B property as needed.
        cls.agent = cls.env["res.partner"].create(
            {"name": "Test Agent X-Co", "agent": True}
        )
        cls.agent.with_company(cls.company_a).sales_profile_id = cls.profile_a

        # Customer linked to the agent (m2m partner_agent_rel).
        cls.customer_xco = cls.env["res.partner"].create(
            {"name": "Customer X-Co", "agent_ids": [(4, cls.agent.id)]}
        )

        # Salesperson must have access to company_b as well, otherwise
        # ``with_user(self.salesperson).with_company(company_b)`` raises
        # an access error before the resolver is exercised.
        cls.salesperson.write({"company_ids": [(4, cls.company_b.id)]})

    # ------------------------------------------------------------------
    # Resolution: agent branch
    # ------------------------------------------------------------------

    def test_agent_branch_resolves_in_condition_company_a(self):
        condition = self.env["partner.commercial.condition"].create(
            {
                "partner_id": self.customer_xco.id,
                "company_id": self.company_a.id,
                "pricelist_id": self.pricelist_a.id,
            }
        )
        self.assertEqual(condition.applicable_profile_id, self.profile_a)
        self.assertEqual(condition.profile_source, "agent")

    def test_agent_branch_unresolved_in_company_b_when_no_property_there(self):
        # Agent has no property in company B, no team, no default → unresolved.
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition"].with_company(
                self.company_b
            ).create(
                {
                    "partner_id": self.customer_xco.id,
                    "company_id": self.company_b.id,
                    "pricelist_id": self.pricelist_b.id,
                }
            )

    def test_agent_branch_resolves_in_company_b_after_property_set(self):
        # Set property in company B → resolution succeeds, profile_b wins.
        self.agent.with_company(self.company_b).sales_profile_id = self.profile_b
        condition = (
            self.env["partner.commercial.condition"]
            .with_company(self.company_b)
            .create(
                {
                    "partner_id": self.customer_xco.id,
                    "company_id": self.company_b.id,
                    "pricelist_id": self.pricelist_b.id,
                }
            )
        )
        self.assertEqual(condition.applicable_profile_id, self.profile_b)

    # ------------------------------------------------------------------
    # Resolution: cross-company candidate is skipped, chain continues
    # ------------------------------------------------------------------

    def test_salesperson_cross_company_skipped_team_default_used(self):
        """Salesperson has profile only in A; condition is in B with
        company default set to profile_b. Resolution must skip the
        cross-company salesperson branch and fall through to the
        company-default branch."""
        salesperson = self.env["res.users"].create(
            {
                "name": "X-Co Salesperson",
                "login": "xco_salesperson",
                "groups_id": [(4, self.env.ref("base.group_user").id)],
            }
        )
        salesperson.partner_id.with_company(
            self.company_a
        ).sales_profile_id = self.profile_a
        # No property in company B for this salesperson.
        customer = self.env["res.partner"].create(
            {"name": "X-Co Cust Salesp", "user_id": salesperson.id}
        )
        self.company_b.default_sales_profile_id = self.profile_b
        condition = (
            self.env["partner.commercial.condition"]
            .with_company(self.company_b)
            .create(
                {
                    "partner_id": customer.id,
                    "company_id": self.company_b.id,
                    "pricelist_id": self.pricelist_b.id,
                }
            )
        )
        self.assertEqual(condition.applicable_profile_id, self.profile_b)
        self.assertEqual(condition.profile_source, "company")

    # ------------------------------------------------------------------
    # Recompute on company change
    # ------------------------------------------------------------------

    def test_changing_condition_company_recomputes_profile(self):
        """Changing ``condition.company_id`` from A to B must recompute
        ``applicable_profile_id`` against the new company."""
        self.agent.with_company(self.company_b).sales_profile_id = self.profile_b
        condition = self.env["partner.commercial.condition"].create(
            {
                "partner_id": self.customer_xco.id,
                "company_id": self.company_a.id,
                "pricelist_id": self.pricelist_a.id,
            }
        )
        self.assertEqual(condition.applicable_profile_id, self.profile_a)
        # Move the condition to company B (also adjust pricelist to keep
        # check_company happy).
        condition.write(
            {"company_id": self.company_b.id, "pricelist_id": self.pricelist_b.id}
        )
        self.assertEqual(condition.applicable_profile_id, self.profile_b)

    # ------------------------------------------------------------------
    # Company default cross-company is ignored
    # ------------------------------------------------------------------

    def test_company_default_cross_company_is_ignored(self):
        """If a migration or backend write sets
        ``company.default_sales_profile_id`` to a profile of another
        company, the resolver must ignore it (returning unresolved).

        ``res.company.default_sales_profile_id`` is a regular column —
        not company_dependent — so we force the inconsistent value
        directly via SQL on the ``res_company`` table, then invalidate
        the cached value on the recordset before exercising the
        resolver.
        """
        self.env.cr.execute(
            "UPDATE res_company SET default_sales_profile_id = %s WHERE id = %s",
            (self.profile_a.id, self.company_b.id),
        )
        self.company_b.invalidate_recordset(["default_sales_profile_id"])
        # Customer with no agent so it falls all the way to default.
        customer = self.env["res.partner"].create({"name": "X-Co Default Test"})
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition"].with_company(
                self.company_b
            ).create(
                {
                    "partner_id": customer.id,
                    "company_id": self.company_b.id,
                    "pricelist_id": self.pricelist_b.id,
                }
            )

    # ------------------------------------------------------------------
    # Constraint blocks invalid stored states
    # ------------------------------------------------------------------

    def test_constraint_blocks_unresolved(self):
        """The ``_check_applicable_profile_resolved`` constraint blocks
        conditions whose compute resolved to an empty profile."""
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition"].with_company(
                self.company_b
            ).create(
                {
                    "partner_id": self.customer_xco.id,
                    "company_id": self.company_b.id,
                    "pricelist_id": self.pricelist_b.id,
                }
            )

    def test_constraint_blocks_cross_company_profile_via_direct_set(self):
        """Defense-in-depth: writing ``applicable_profile_id`` directly to a
        profile of another company must be caught by the constraint.

        Uses Python attribute assignment (which becomes a normal ORM
        ``write({"applicable_profile_id": ...})``) instead of SQL
        backdoor + ``invalidate_recordset`` — the latter triggers a flush
        before invalidation in Odoo 16, which would let the resolver
        recompute the correct value back, masking the test.
        """
        self.agent.with_company(self.company_b).sales_profile_id = self.profile_b
        condition = (
            self.env["partner.commercial.condition"]
            .with_company(self.company_b)
            .create(
                {
                    "partner_id": self.customer_xco.id,
                    "company_id": self.company_b.id,
                    "pricelist_id": self.pricelist_b.id,
                }
            )
        )
        # Direct write of a cross-company profile must trip the
        # constraint immediately (this branch covers paths where the
        # ``applicable_profile_id`` ends up overridden manually,
        # bypassing the resolver — e.g. data fixes, custom imports).
        with self.assertRaises(ValidationError):
            condition.applicable_profile_id = self.profile_a

    def test_check_internal_compatibility_noop_when_no_context_resolves(self):
        """When the internal profile compatibility chain can't resolve a
        same-company profile (no salesperson, no team profile, no company
        default), the check is a no-op — without an expected reference,
        comparison is meaningless. Covers the empty-return branch of
        ``_resolve_compatibility_profile``.
        """
        # Build an internal profile in company B and pin it via agent so
        # the condition resolves cleanly (constraint passes).
        internal_b = (
            self.env["tr.sales.profile"]
            .with_company(self.company_b)
            .create(
                {
                    "name": "Internal B",
                    "profile_type": "internal",
                    "company_id": self.company_b.id,
                    "pricelist_ids": [(6, 0, [self.pricelist_b.id])],
                    "cash_discount_max": 8.0,
                    "fob_discount_max": 5.0,
                    "cash_term_avg_days_max": 30,
                    "manager_extra_limit": 0.0,
                    "rule_ids": self._placeholder_rule_vals("internal"),
                }
            )
        )
        self.agent.with_company(self.company_b).sales_profile_id = internal_b
        # Customer with the agent → condition resolves via agent → no
        # cross-company; constraint passes.
        cust = self.env["res.partner"].create(
            {
                "name": "Internal No-Context Cust",
                "agent_ids": [(4, self.agent.id)],
            }
        )
        condition = (
            self.env["partner.commercial.condition"]
            .with_company(self.company_b)
            .create(
                {
                    "partner_id": cust.id,
                    "company_id": self.company_b.id,
                    "pricelist_id": self.pricelist_b.id,
                }
            )
        )
        self.assertEqual(condition.applicable_profile_id, internal_b)
        cust.commercial_condition_id = condition
        # Order without salesperson/team in company_b, and no company
        # default in B → ``_resolve_compatibility_profile`` returns
        # (empty, "") → ``_check_internal_context_compatibility`` is a
        # no-op.
        order = (
            self.env["sale.order"]
            .with_company(self.company_b)
            .create(
                {
                    "partner_id": cust.id,
                    "company_id": self.company_b.id,
                    "pricelist_id": self.pricelist_b.id,
                }
            )
        )
        self.assertEqual(order.sales_profile_id, internal_b)
        # Should not raise: chain has nothing to compare against.
        order._check_profile_consistency()

    def test_cascade_blocks_when_remove_agent_with_no_fallback(self):
        """Removing the only configuration that resolved a condition's
        profile must raise ``ValidationError`` immediately.

        The condition was resolved via the agent in company B. Removing
        the agent from the customer triggers a cascading recompute of
        ``applicable_profile_id`` on the condition. With no salesperson,
        team or company default in company B, the recompute resolves to
        an empty profile and ``_check_applicable_profile_resolved``
        blocks the underlying ``write`` on the partner. This documents
        the integrity-first decision: invalid cascade state surfaces as
        an error at the source operation rather than persisting as a
        stale condition.
        """
        self.agent.with_company(self.company_b).sales_profile_id = self.profile_b
        condition = (
            self.env["partner.commercial.condition"]
            .with_company(self.company_b)
            .create(
                {
                    "partner_id": self.customer_xco.id,
                    "company_id": self.company_b.id,
                    "pricelist_id": self.pricelist_b.id,
                }
            )
        )
        self.assertEqual(condition.applicable_profile_id, self.profile_b)
        with self.assertRaises(ValidationError):
            self.customer_xco.agent_ids = [(5,)]

    # ------------------------------------------------------------------
    # profile_source semantics
    # ------------------------------------------------------------------

    def test_profile_source_false_when_unresolved(self):
        """When nothing resolves, ``profile_source`` must be False, not
        ``"company"`` (which would falsely advertise that the company
        default applied)."""
        # No agent, no salesperson, no team, no default set.
        customer = self.env["res.partner"].create({"name": "X-Co Empty"})
        # Bypass the constraint so we can inspect the empty resolution.
        condition = (
            self.env["partner.commercial.condition"]
            .with_context(skip_profile_resolution_check=True)
            .with_company(self.company_b)
            .create(
                {
                    "partner_id": customer.id,
                    "company_id": self.company_b.id,
                    "pricelist_id": self.pricelist_b.id,
                }
            )
        )
        self.assertFalse(condition.applicable_profile_id)
        self.assertFalse(condition.profile_source)

    # ------------------------------------------------------------------
    # Pre-create validation uses target company, not env.company
    # ------------------------------------------------------------------

    def test_create_cross_company_validates_against_target_company(self):
        """Creating a condition for company B with ``env.company = A``
        must validate discounts against profile_b's limits, not
        profile_a's. profile_b allows up to 5% cash; profile_a only
        3%. seller_discount=4 should pass (4 < 5)."""
        self.agent.with_company(self.company_b).sales_profile_id = self.profile_b
        # env.company stays at A; vals targets B. Pre-fix this would
        # validate against profile_a's stricter limits and raise.
        # Use a non-director user so the validation actually runs (directors
        # bypass discount validation).
        condition = (
            self.env["partner.commercial.condition"]
            .with_user(self.salesperson)
            .with_company(self.company_a)
            .create(
                {
                    "partner_id": self.customer_xco.id,
                    "company_id": self.company_b.id,
                    "pricelist_id": self.pricelist_b.id,
                    "cash_discount": 4.0,
                }
            )
        )
        self.assertEqual(condition.applicable_profile_id, self.profile_b)
        self.assertEqual(condition.cash_discount, 4.0)

    def test_create_cross_company_blocks_when_target_profile_is_strict(self):
        """Same setup but the discount exceeds the target company's
        profile. cash_discount=10 must raise (10 > 5 from profile_b)."""
        self.agent.with_company(self.company_b).sales_profile_id = self.profile_b
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition"].with_user(
                self.salesperson
            ).with_company(self.company_a).create(
                {
                    "partner_id": self.customer_xco.id,
                    "company_id": self.company_b.id,
                    "pricelist_id": self.pricelist_b.id,
                    "cash_discount": 10.0,
                }
            )

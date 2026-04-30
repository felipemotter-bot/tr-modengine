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
        # ``commercial_condition_id`` is company_dependent — write in the
        # condition's company so the order's compute (which reads via
        # ``with_company(order.company_id)`` after the multi-company fix)
        # finds the link.
        cust.with_company(self.company_b).commercial_condition_id = condition
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

    # ------------------------------------------------------------------
    # Order's ``commercial_condition_id`` follows the order's company
    # ------------------------------------------------------------------

    def test_order_picks_partner_condition_in_order_company(self):
        """Regression: ``sale.order._compute_commercial_condition_id`` used
        to read ``partner.effective_condition_id`` without ``with_company``,
        so an order in company B picked up the condition stored for
        ``env.company`` instead of the order's company. The fix calls
        ``with_company(order.company_id)`` before reading the partner's
        condition.
        """
        # Agent has profile in both companies so the per-company
        # constraint on each condition passes.
        self.agent.with_company(self.company_b).sales_profile_id = self.profile_b
        cond_a = self.env["partner.commercial.condition"].create(
            {
                "partner_id": self.customer_xco.id,
                "company_id": self.company_a.id,
                "pricelist_id": self.pricelist_a.id,
            }
        )
        cond_b = (
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
        self.customer_xco.with_company(self.company_a).commercial_condition_id = cond_a
        self.customer_xco.with_company(self.company_b).commercial_condition_id = cond_b
        # Order in company B with ``env.company`` = A. Without the fix
        # in ``_compute_commercial_condition_id``, this would read the
        # partner's condition through ``env.company`` (= A) and pick
        # ``cond_a``. The fix forces ``with_company(order.company_id)``
        # so the order picks ``cond_b``.
        order = (
            self.env["sale.order"]
            .with_company(self.company_a)
            .create(
                {
                    "partner_id": self.customer_xco.id,
                    "company_id": self.company_b.id,
                    "pricelist_id": self.pricelist_b.id,
                }
            )
        )
        self.assertEqual(order.commercial_condition_id, cond_b)

    def _setup_partner_with_dual_company_conditions(self):
        """Helper: customer with one condition per company, profiles set."""
        self.agent.with_company(self.company_b).sales_profile_id = self.profile_b
        cond_a = self.env["partner.commercial.condition"].create(
            {
                "partner_id": self.customer_xco.id,
                "company_id": self.company_a.id,
                "pricelist_id": self.pricelist_a.id,
            }
        )
        cond_b = (
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
        self.customer_xco.with_company(self.company_a).commercial_condition_id = cond_a
        self.customer_xco.with_company(self.company_b).commercial_condition_id = cond_b
        return cond_a, cond_b

    def test_group_condition_wizard_clears_in_override_company(self):
        """``tr.group.condition.wizard`` with ``action='inherit'`` must
        clear the partner's ``commercial_condition_id`` in the company
        that owns the override condition, even when ``env.company`` is
        a different company. Without the fix, the wizard cleared
        ``env.company``'s link and left the actual override silently in
        place.
        """
        cond_a, cond_b = self._setup_partner_with_dual_company_conditions()
        # Run the wizard with env.company = A but pointing at the
        # override condition in company B.
        wizard = (
            self.env["tr.group.condition.wizard"]
            .with_company(self.company_a)
            .create(
                {
                    "partner_id": self.customer_xco.id,
                    "own_condition_id": cond_b.id,
                    "action": "inherit",
                }
            )
        )
        wizard.action_confirm()
        # Company B's link must be cleared. Company A's must stay.
        self.assertFalse(
            self.customer_xco.with_company(self.company_b).commercial_condition_id
        )
        self.assertEqual(
            self.customer_xco.with_company(self.company_a).commercial_condition_id,
            cond_a,
        )

    # ------------------------------------------------------------------
    # Multi-company audit fixes (PR follow-up to PR #58)
    # ------------------------------------------------------------------

    def test_action_create_commercial_condition_in_env_company(self):
        """``res.partner.action_create_commercial_condition`` must create
        the condition in the user's current company and link it under
        that same company on the partner. Run with ``env.company`` = B
        and verify both ends land in B.
        """
        # Default profile in company B so the new condition resolves
        # cleanly via the company-default fallback (the partner has no
        # agent/team/salesperson by design).
        self.company_b.default_sales_profile_id = self.profile_b
        partner = self.env["res.partner"].create({"name": "Manual Cond Cust"})
        partner.with_company(self.company_b).action_create_commercial_condition()
        condition_b = partner.with_company(self.company_b).commercial_condition_id
        self.assertTrue(condition_b)
        self.assertEqual(condition_b.company_id, self.company_b)
        # Company A must NOT have inherited the link.
        self.assertFalse(partner.with_company(self.company_a).commercial_condition_id)

    def test_action_remove_override_clears_in_env_company_only(self):
        """``action_remove_override_condition`` clears the override in
        the user's current company without touching other companies.
        Setup: partner with conditions in both A and B. Run remove with
        ``env.company`` = B. A's link must remain.
        """
        cond_a, cond_b = self._setup_partner_with_dual_company_conditions()
        self.customer_xco.with_company(
            self.company_b
        ).action_remove_override_condition()
        self.assertFalse(
            self.customer_xco.with_company(self.company_b).commercial_condition_id
        )
        self.assertEqual(
            self.customer_xco.with_company(self.company_a).commercial_condition_id,
            cond_a,
        )

    def test_sync_to_partners_finds_links_in_condition_company(self):
        """``_sync_to_partners`` must search/filter
        ``res.partner.commercial_condition_id`` (company_dependent) in
        the condition's company. Run sync on a B condition with
        ``env.company`` = A; the partner linked in B must be found and
        synced (verified by checking that pricelist propagated).
        """
        cond_a, cond_b = self._setup_partner_with_dual_company_conditions()
        # Change cond_b's pricelist so the sync has something to copy.
        new_pl_b = self.env["product.pricelist"].create(
            {"name": "PL B Alt", "company_id": self.company_b.id}
        )
        # Add the new pricelist to profile_b so the constraint passes.
        self.profile_b.with_company(self.company_b).pricelist_ids = [(4, new_pl_b.id)]
        cond_b.with_company(self.company_b).pricelist_id = new_pl_b
        # Trigger sync from env.company = A. Without the with_company in
        # _sync_to_partners, the search would not see partner→cond_b
        # link (it's stored as ir.property in company B).
        cond_b.with_company(self.company_a)._sync_to_partners()
        # Property in company B must reflect the new pricelist.
        self.assertEqual(
            self.customer_xco.with_company(self.company_b).property_product_pricelist,
            new_pl_b,
        )

    def test_get_partner_avg_order_amount_filters_by_company(self):
        """``_get_partner_avg_order_amount`` must filter by the
        ``company`` kwarg, not ``env.company``. Confirmed sale order in
        company A; the call with ``company=A`` returns its amount, with
        ``company=B`` returns zero — proves the filter actually uses
        the kwarg.
        """
        # The ``customer_xco`` already has an agent with profile in A;
        # build a plain order in A and force it into ``sale`` state via
        # sudo so the search picks it up (we don't need to exercise the
        # full confirmation flow here — just have a record matching the
        # search domain).
        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer_xco.id,
                "company_id": self.company_a.id,
                "pricelist_id": self.pricelist_a.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "product_uom_qty": 1.0,
                            "price_unit": 100.0,
                        },
                    )
                ],
            }
        )
        order.sudo().write({"state": "sale"})
        Cond = self.env["partner.commercial.condition"]
        avg_a = Cond._get_partner_avg_order_amount(
            self.customer_xco, company=self.company_a
        )
        avg_b = Cond._get_partner_avg_order_amount(
            self.customer_xco, company=self.company_b
        )
        self.assertGreater(avg_a, 0.0)
        self.assertEqual(avg_b, 0.0)

    def test_action_create_override_copies_group_condition_in_env_company(self):
        """``action_create_override_condition`` must read the group's
        ``commercial_condition_id`` (company_dependent) in the user's
        current company and copy values to the new override anchored in
        that same company.
        """
        # Default profile in B so the group's and override condition
        # both resolve cleanly.
        self.company_b.default_sales_profile_id = self.profile_b
        # Group head with its own condition in company B carrying a
        # distinctive cash_discount value to verify the copy.
        group = self.env["res.partner"].create(
            {"name": "Group Head", "is_company": True}
        )
        group_cond_b = (
            self.env["partner.commercial.condition"]
            .with_company(self.company_b)
            .create(
                {
                    "partner_id": group.id,
                    "company_id": self.company_b.id,
                    "pricelist_id": self.pricelist_b.id,
                    "cash_discount": 2.5,
                }
            )
        )
        group.with_company(self.company_b).commercial_condition_id = group_cond_b
        # Member partner linked to the group.
        member = self.env["res.partner"].create(
            {"name": "Group Member", "company_group_id": group.id}
        )
        # Trigger override creation in company B.
        member.with_company(self.company_b).action_create_override_condition()
        override = member.with_company(self.company_b).commercial_condition_id
        self.assertTrue(override)
        self.assertEqual(override.company_id, self.company_b)
        # The override copied the group's cash_discount from company B.
        self.assertEqual(override.cash_discount, 2.5)
        # Company A must remain empty.
        self.assertFalse(member.with_company(self.company_a).commercial_condition_id)

    def test_check_agent_line_profiles_reads_in_order_company(self):
        """End-to-end: ``_check_agent_line_profiles`` is exercised via
        ``_check_profile_consistency`` on a real order with an agent
        line. Order in company B, ``env.company`` = A; agent has
        ``profile_b`` in B. Without ``with_company(self.company_id)``
        in the check, the agent's profile read would fall back to A's
        empty value and either silently skip the comparison or raise.
        """
        # Commission is required on the agent partner so the order line
        # can persist its agent record (NOT NULL on commission_id).
        commission = self.env["commission"].create(
            {"name": "X-Co Commission", "commission_type": "fixed", "fix_qty": 0.0}
        )
        self.agent.commission_id = commission
        self.agent.with_company(self.company_b).sales_profile_id = self.profile_b
        cust = self.env["res.partner"].create(
            {
                "name": "Compat Agent Cust",
                "agent_ids": [(4, self.agent.id)],
            }
        )
        cust.with_company(self.company_b).commercial_condition_id = (
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
        # The product_a's pricelist_b setup: ensure profile_b accepts it
        # (already does — pricelist_ids includes pricelist_b).
        order = (
            self.env["sale.order"]
            .with_company(self.company_a)
            .create(
                {
                    "partner_id": cust.id,
                    "company_id": self.company_b.id,
                    "pricelist_id": self.pricelist_b.id,
                    "order_line": [
                        (
                            0,
                            0,
                            {
                                "product_id": self.product_a.id,
                                "product_uom_qty": 1.0,
                                "price_unit": 100.0,
                            },
                        )
                    ],
                }
            )
        )
        # Confirm that the agent ended up on the order line (commission
        # propagation from partner.agent_ids).
        line = order.order_line
        self.assertTrue(line.agent_ids, "line should have an agent for the test")
        # Exercise the check via the public consistency entrypoint —
        # same path that ``action_confirm`` walks. With the fix, the
        # agent's profile is read in company B and matches profile_b;
        # without it, the read in env.company A would return empty (no
        # property) and the comparison would silently skip on the
        # ``if agent_profile`` guard, masking the regression — that's
        # why the test asserts ``agent_ids`` is populated and that the
        # call below does not raise.
        order._check_profile_consistency()

    # ------------------------------------------------------------------
    # Branch coverage: short-circuit paths in resolution chain
    # ------------------------------------------------------------------

    def test_partner_team_cross_company_skipped_default_pricelist_fallback(self):
        """Covers two short-circuit branches not exercised by the main
        chain tests:

        - ``_resolve_applicable_profile_and_source``: ``partner.team_id``
          has a profile in company A; condition is in company B. The
          team branch is skipped and resolution falls through to the
          company default.
        - ``_resolve_default_pricelist_for_partner``: the resolved
          profile has no ``pricelist_ids``; the resolver falls back to
          ``product.pricelist.search`` filtered by company.
        """
        team_a = self.env["crm.team"].create(
            {"name": "X-Co Team A", "company_id": self.company_a.id}
        )
        team_a.sales_profile_id = self.profile_a
        # Default profile in company B *without* pricelist_ids so the
        # default-pricelist resolver falls into the search branch.
        default_b_no_pl = (
            self.env["tr.sales.profile"]
            .with_company(self.company_b)
            .create(
                {
                    "name": "Default B No Pricelist",
                    "profile_type": "internal",
                    "company_id": self.company_b.id,
                    "pricelist_ids": [(6, 0, [])],
                    "cash_discount_max": 0.0,
                    "fob_discount_max": 0.0,
                    "cash_term_avg_days_max": 30,
                    "manager_extra_limit": 0.0,
                    "rule_ids": self._placeholder_rule_vals("internal"),
                }
            )
        )
        self.company_b.default_sales_profile_id = default_b_no_pl
        customer = self.env["res.partner"].create(
            {"name": "X-Co Team Customer", "team_id": team_a.id}
        )
        Condition = self.env["partner.commercial.condition"].with_company(
            self.company_b
        )
        condition = Condition.create(
            {
                "partner_id": customer.id,
                "company_id": self.company_b.id,
                "pricelist_id": self.pricelist_b.id,
            }
        )
        self.assertEqual(condition.applicable_profile_id, default_b_no_pl)
        self.assertEqual(condition.profile_source, "company")
        # Profile resolved has no pricelist_ids → fallback search by company.
        pricelist = Condition._resolve_default_pricelist_for_partner(
            customer, company=self.company_b
        )
        self.assertTrue(pricelist)
        self.assertIn(pricelist.company_id.id or False, (self.company_b.id, False))

    def test_resolve_compatibility_profile_salesperson_team_cross_company(self):
        """Covers ``sale_order._resolve_compatibility_profile`` short-
        circuits in the salesperson branch:

        - ``self.user_id`` is truthy.
        - Salesperson has profile only in company A (cross-company for B).
        - Salesperson's ``sale_team_id`` exists with profile in A
          (cross-company) → ``L1096`` short-circuit, branch skipped.
        - Order's ``team_id`` is empty → ``L1101`` false branch.
        - Falls through to the company-default branch.
        """
        team_a = self.env["crm.team"].create(
            {"name": "Salesperson Team A", "company_id": self.company_a.id}
        )
        team_a.sales_profile_id = self.profile_a
        salesperson = self.env["res.users"].create(
            {
                "name": "Salesperson Cross-Co Team",
                "login": "xco_sp_team",
                "groups_id": [(4, self.env.ref("base.group_user").id)],
                "company_ids": [(4, self.company_b.id), (4, self.company_a.id)],
                "company_id": self.company_b.id,
                "sale_team_id": team_a.id,
            }
        )
        salesperson.partner_id.with_company(
            self.company_a
        ).sales_profile_id = self.profile_a
        self.company_b.default_sales_profile_id = self.profile_b
        # Customer with agent in company B so the condition resolves
        # cleanly via agent → constraint passes; the order uses that
        # condition and we only exercise the compatibility resolver.
        self.agent.with_company(self.company_b).sales_profile_id = self.profile_b
        cust = self.env["res.partner"].create(
            {
                "name": "X-Co Compat Customer",
                "agent_ids": [(4, self.agent.id)],
            }
        )
        cust.with_company(self.company_b).commercial_condition_id = (
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
        order = (
            self.env["sale.order"]
            .with_company(self.company_b)
            .create(
                {
                    "partner_id": cust.id,
                    "company_id": self.company_b.id,
                    "pricelist_id": self.pricelist_b.id,
                    "user_id": salesperson.id,
                    "team_id": False,
                }
            )
        )
        expected, source = order._resolve_compatibility_profile()
        self.assertEqual(expected, self.profile_b)
        self.assertIn("company default", source)

    def test_resolve_compatibility_profile_no_user_no_team_no_default(self):
        """Covers ``sale_order._resolve_compatibility_profile`` empty
        return path:

        - Order with ``user_id=False`` → ``L1089`` false branch.
        - Order with ``team_id=False`` → ``L1101`` false branch.
        - Company default cleared in company B → no resolution.
        - Resolver returns ``(empty_recordset, "")`` so the
          compatibility check becomes a no-op.
        """
        # Customer needs a resolvable condition for the order's
        # constraint, but the compatibility resolver is independent of
        # that — it traverses user/team/default.
        self.agent.with_company(self.company_b).sales_profile_id = self.profile_b
        cust = self.env["res.partner"].create(
            {
                "name": "X-Co No-Context Customer",
                "agent_ids": [(4, self.agent.id)],
            }
        )
        cust.with_company(self.company_b).commercial_condition_id = (
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
        # Ensure no company default in B.
        self.company_b.default_sales_profile_id = False
        order = (
            self.env["sale.order"]
            .with_company(self.company_b)
            .create(
                {
                    "partner_id": cust.id,
                    "company_id": self.company_b.id,
                    "pricelist_id": self.pricelist_b.id,
                    "user_id": False,
                    "team_id": False,
                }
            )
        )
        expected, source = order._resolve_compatibility_profile()
        self.assertFalse(expected)
        self.assertEqual(source, "")

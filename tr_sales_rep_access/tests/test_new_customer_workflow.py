# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import ValidationError

from .common import SalesRepAccessTestCommon


class TestNewCustomerWorkflow(SalesRepAccessTestCommon):
    """Draft → Active workflow for customers created by sales reps.

    When a rep creates a new commercial partner, it is forced to the
    Draft stage regardless of any explicit ``stage_id`` in vals and
    immediately goes through the tier validation workflow. The
    partner cannot be used in ``sale.order`` until a Sales Manager
    validates the tier and promotes the stage to Active.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.draft_stage = cls.env.ref("partner_stage.partner_stage_draft")
        cls.active_stage = cls.env.ref("partner_stage.partner_stage_active")

    # ----------------------------------------------------------------
    # Stage forcing on create
    # ----------------------------------------------------------------

    def test_rep_creates_customer_in_draft(self):
        """Rep-created customer with an agent starts in Draft."""
        customer = (
            self.env["res.partner"]
            .with_user(self.user_u1)
            .create(
                {
                    "name": "Rep New Customer",
                    "agent_ids": [(6, 0, [self.agent_a1.id])],
                }
            )
        )
        self.assertEqual(customer.stage_id, self.draft_stage)
        self.assertEqual(customer.state, "draft")

    def test_admin_creates_customer_in_default_stage(self):
        """Admin-created customer keeps the system default (Active)."""
        customer = self.env["res.partner"].create(
            {
                "name": "Admin New Customer",
                "agent_ids": [(6, 0, [self.agent_a1.id])],
            }
        )
        self.assertEqual(customer.stage_id, self.active_stage)
        self.assertEqual(customer.state, "confirmed")

    def test_rep_creates_child_contact_does_not_force_draft(self):
        """Child contacts are not forced to Draft by the rep override."""
        child = (
            self.env["res.partner"]
            .with_user(self.user_u1)
            .create(
                {
                    "name": "Contact of C1",
                    "parent_id": self.customer_c1.id,
                }
            )
        )
        # stage_id has its own default (Active) on partner_stage.
        self.assertEqual(child.stage_id, self.active_stage)

    def test_rep_explicit_stage_is_overridden(self):
        """Rep cannot bypass Draft by passing stage_id=active in vals."""
        customer = (
            self.env["res.partner"]
            .with_user(self.user_u1)
            .create(
                {
                    "name": "Try to Skip",
                    "agent_ids": [(6, 0, [self.agent_a1.id])],
                    "stage_id": self.active_stage.id,
                }
            )
        )
        self.assertEqual(customer.stage_id, self.draft_stage)

    # ----------------------------------------------------------------
    # Tier triggering
    # ----------------------------------------------------------------

    def test_tier_reviews_created_on_rep_customer_create(self):
        """Rep creating a customer triggers tier reviews immediately."""
        customer = (
            self.env["res.partner"]
            .with_user(self.user_u1)
            .create(
                {
                    "name": "Customer With Tier",
                    "agent_ids": [(6, 0, [self.agent_a1.id])],
                }
            )
        )
        self.assertTrue(
            customer.review_ids,
            "Expected at least one tier.review after rep-created customer.",
        )

    def test_rep_child_contact_does_not_trigger_tier(self):
        """The tier_definition's ``parent_id = False`` filter skips children.

        Creates a child that already matches the other two filters
        (``state = draft`` and ``agent_ids != False``) and calls
        ``request_validation()`` explicitly — the rep ``create``
        override normally filters children out, so we bypass it to
        prove that the tier definition itself also refuses to fire
        on children (defense in depth).
        """
        child = (
            self.env["res.partner"]
            .with_user(self.user_u1)
            .create(
                {
                    "name": "Child Not In Tier",
                    "parent_id": self.customer_c1.id,
                    "agent_ids": [(6, 0, [self.agent_a1.id])],
                }
            )
        )
        # Commercial sync from the parent may reset ``stage_id`` back
        # to Active via partner_tier_validation's write override; force
        # Draft here so the tier's ``state = draft`` filter wouldn't
        # mask the ``parent_id = False`` filter under test.
        child.sudo().stage_id = self.draft_stage
        child.invalidate_recordset()
        self.assertEqual(child.state, "draft")
        child.sudo().request_validation()
        self.assertFalse(
            child.review_ids,
            "Tier must not attach to a child contact regardless of "
            "explicit request_validation() calls.",
        )

    def test_rep_creates_customer_without_agent_ids_auto_populates(self):
        """Rep creating a commercial without agent_ids is auto-linked.

        The project does not install ``sale_commission_agent_restrict``,
        so nothing else would set ``agent_ids`` and the tier would
        never fire. The ``create`` override covers the gap by
        injecting ``env.user.partner_id`` for rep users on new
        commercial partners.
        """
        customer = (
            self.env["res.partner"]
            .with_user(self.user_u1)
            .create({"name": "Rep Customer Without Explicit Agent"})
        )
        self.assertEqual(
            customer.stage_id,
            self.draft_stage,
            "Rep-created customer must be forced to Draft.",
        )
        self.assertIn(
            self.agent_a1,
            customer.agent_ids,
            "Override must have injected the acting rep's partner.",
        )
        self.assertTrue(
            customer.review_ids,
            "Tier reviews must be created for auto-linked customer.",
        )

    def test_rep_explicit_agent_ids_is_not_overwritten(self):
        """If the rep explicitly passes agent_ids, the override is a no-op.

        Uses ``agent_a1`` (the acting rep's own partner) so the
        ``res.partner`` rule still grants the rep read access after
        create. The property under test is that the override does
        not duplicate or append to an already-populated
        ``agent_ids``.
        """
        customer = (
            self.env["res.partner"]
            .with_user(self.user_u1)
            .create(
                {
                    "name": "Rep Customer With Explicit Agent",
                    "agent_ids": [(6, 0, [self.agent_a1.id])],
                }
            )
        )
        self.assertEqual(customer.agent_ids, self.agent_a1)

    def test_rep_create_with_empty_agent_ids_raises(self):
        """Rep passing ``agent_ids=[]`` via RPC must be rejected.

        Without this guard, the customer would be created in
        Draft but with no agents, which would (a) hide it from
        the rep via the partner record rule and (b) skip the
        tier because the definition domain requires
        ``agent_ids != False``.
        """
        with self.assertRaises(ValidationError):
            self.env["res.partner"].with_user(self.user_u1).create(
                {
                    "name": "Rep Customer Empty Agents",
                    "agent_ids": [(6, 0, [])],
                }
            )

    def test_rep_create_with_link_command_passes(self):
        """Legacy short ``(4, rep_partner_id)`` command is accepted."""
        customer = (
            self.env["res.partner"]
            .with_user(self.user_u1)
            .create(
                {
                    "name": "Rep Customer With Link Command",
                    "agent_ids": [(4, self.agent_a1.id)],
                }
            )
        )
        self.assertEqual(customer.agent_ids, self.agent_a1)

    def test_rep_create_with_list_of_lists_command_passes(self):
        """JSON-RPC sends commands as lists of lists; must be accepted.

        The normalization step converts each inner ``list`` to a
        ``tuple`` before the whitelist check. Without it, a
        legitimate payload from a JSON-RPC client would be
        rejected purely on wire format.
        """
        customer = (
            self.env["res.partner"]
            .with_user(self.user_u1)
            .create(
                {
                    "name": "Rep Customer JSON-RPC shape",
                    "agent_ids": [[6, 0, [self.agent_a1.id]]],
                }
            )
        )
        self.assertEqual(customer.agent_ids, self.agent_a1)

    def test_rep_create_with_canonical_link_command_passes(self):
        """Canonical ``(4, rep_partner_id, 0)`` 3-tuple is accepted.

        ``odoo.fields.Command.link()`` returns this 3-tuple form;
        the guard must match it, not only the legacy short form.
        """
        customer = (
            self.env["res.partner"]
            .with_user(self.user_u1)
            .create(
                {
                    "name": "Rep Customer With Canonical Link",
                    "agent_ids": [(4, self.agent_a1.id, 0)],
                }
            )
        )
        self.assertEqual(customer.agent_ids, self.agent_a1)

    def test_rep_create_with_clear_command_raises(self):
        """``(5,)`` (clear) leaves agent_ids empty and is rejected."""
        with self.assertRaises(ValidationError):
            self.env["res.partner"].with_user(self.user_u1).create(
                {
                    "name": "Rep Customer With Clear Command",
                    "agent_ids": [(5,)],
                }
            )

    def test_rep_create_with_foreign_agent_raises(self):
        """Rep assigning another rep as agent is rejected.

        Business rule: a customer created by a rep belongs to
        that rep. Passing a different rep via RPC/import is a
        bypass attempt and must be blocked server-side.
        """
        with self.assertRaises(ValidationError):
            self.env["res.partner"].with_user(self.user_u1).create(
                {
                    "name": "Rep Customer Foreign Agent",
                    "agent_ids": [(6, 0, [self.agent_a2.id])],
                }
            )

    def test_rep_child_contact_inherits_agent_from_parent(self):
        """Child contacts are not auto-linked by the override.

        ``agent_ids`` on a child ends up equal to the commercial
        parent's ``agent_ids`` because the core's commercial-field
        sync copies the value from the parent. The property under
        test is that the override does NOT inject the acting rep
        on its own — if it did, a child created under a customer
        whose agent is someone else would be polluted.
        """
        child = (
            self.env["res.partner"]
            .with_user(self.user_u1)
            .create(
                {
                    "name": "Child Inherits Parent Agent",
                    "parent_id": self.customer_c1.id,
                }
            )
        )
        # customer_c1 has agent_a1 in agent_ids (see common.py);
        # the child inherits via commercial sync, not via the
        # override's auto-populate.
        self.assertEqual(child.agent_ids, self.customer_c1.agent_ids)

    def test_admin_customer_without_agent_does_not_trigger_tier(self):
        """Admin-created customer without an agent stays out of the tier."""
        customer = self.env["res.partner"].create({"name": "No Agent Customer"})
        self.assertFalse(customer.review_ids)

    # ----------------------------------------------------------------
    # Stage transition gated by tier
    # ----------------------------------------------------------------

    def test_stage_transition_to_active_requires_tier_validation(self):
        """Promoting stage to Active without validated tier raises."""
        customer = (
            self.env["res.partner"]
            .with_user(self.user_u1)
            .create(
                {
                    "name": "Pending Validation",
                    "agent_ids": [(6, 0, [self.agent_a1.id])],
                }
            )
        )
        with self.assertRaises(ValidationError):
            customer.write({"stage_id": self.active_stage.id})

    def test_stage_transition_works_after_tier_validation(self):
        """Manager validates tier and stage can then be promoted."""
        customer = (
            self.env["res.partner"]
            .with_user(self.user_u1)
            .create(
                {
                    "name": "To Be Approved",
                    "agent_ids": [(6, 0, [self.agent_a1.id])],
                }
            )
        )
        # Bounce back to admin env (implies group_sales_manager via
        # group_sales_director via group_system) so validate_tier
        # really approves the review. ``invalidate_recordset`` mirrors
        # the upstream partner_tier_validation test — forces a reload
        # before inspecting review_ids.
        admin = self.env.ref("base.user_admin")
        customer = customer.with_user(admin)
        customer.invalidate_recordset()
        customer.validate_tier()
        customer.write({"stage_id": self.active_stage.id})
        self.assertEqual(customer.state, "confirmed")

    # ----------------------------------------------------------------
    # sale.order guards
    # ----------------------------------------------------------------

    def _draft_customer(self):
        """Helper: rep-created customer stuck in Draft for these tests."""
        return (
            self.env["res.partner"]
            .with_user(self.user_u1)
            .create(
                {
                    "name": "Draft Customer",
                    "agent_ids": [(6, 0, [self.agent_a1.id])],
                }
            )
        )

    def test_sale_order_blocked_with_draft_customer(self):
        """Cannot create an order for a Draft customer."""
        draft = self._draft_customer()
        with self.assertRaises(ValidationError):
            self.env["sale.order"].create({"partner_id": draft.id})

    def test_sale_order_blocked_with_child_of_draft_customer(self):
        """Cannot create an order using a child contact of Draft customer."""
        draft = self._draft_customer()
        child = self.env["res.partner"].create(
            {"name": "Child Of Draft", "parent_id": draft.id}
        )
        with self.assertRaises(ValidationError):
            self.env["sale.order"].create({"partner_id": child.id})

    def test_sale_order_blocked_on_partner_change_to_draft(self):
        """Switching an existing order's partner to a Draft one raises."""
        order = self._make_order(self.customer_c1)
        draft = self._draft_customer()
        with self.assertRaises(ValidationError):
            order.write({"partner_id": draft.id})

    def test_action_confirm_blocked_when_customer_demoted_to_draft(self):
        """Confirming an order fails if the customer was demoted to Draft.

        The @api.constrains fires on partner_id change but not on a
        state change of the partner itself, so this scenario must be
        caught by the action_confirm override.
        """
        order = self._make_order(self.customer_c1)
        self.customer_c1.write({"stage_id": self.draft_stage.id})
        with self.assertRaises(ValidationError):
            order.action_confirm()

    def test_sale_order_allowed_after_partner_promoted(self):
        """After tier validation + stage promotion, orders work."""
        customer = (
            self.env["res.partner"]
            .with_user(self.user_u1)
            .create(
                {
                    "name": "Approved Flow",
                    "agent_ids": [(6, 0, [self.agent_a1.id])],
                }
            )
        )
        admin = self.env.ref("base.user_admin")
        customer = customer.with_user(admin)
        customer.invalidate_recordset()
        customer.validate_tier()
        customer.write({"stage_id": self.active_stage.id})
        order = self.env["sale.order"].create({"partner_id": customer.id})
        self.assertTrue(order.exists())

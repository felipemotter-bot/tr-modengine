# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestTierApproval(CommercialPolicyTestCommon):
    """Tier approval tests using real XML tier definitions (group + has_comment)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

        # Grant all-leads access so manager/director can see any order
        all_leads = cls.env.ref("sales_team.group_sale_salesman_all_leads")
        cls.manager_user.groups_id = [(4, all_leads.id)]
        cls.director_user.groups_id = [(4, all_leads.id)]

        # Verify the XML tier definitions exist by XML ID
        cls.xml_tier_manager = cls.env.ref(
            "tr_commercial_policy.tier_def_discount_manager"
        )
        cls.xml_tier_director = cls.env.ref(
            "tr_commercial_policy.tier_def_discount_director"
        )

    def _create_clean_order(self, **kwargs):
        """Create an order without fiscal operation to avoid l10n_br issues."""
        vals = {"fiscal_operation_id": False}
        vals.update(kwargs)
        return self._create_order(**vals)

    def _approve_via_comment_wizard(self, order, approver):
        """Simulate the full validate_tier + comment wizard flow."""
        # validate_tier returns wizard action when has_comment=True
        action = order.with_user(approver).validate_tier()
        self.assertEqual(action.get("res_model"), "comment.wizard")
        # Create and execute the comment wizard
        wizard_ctx = action.get("context", {})
        wizard = (
            self.env["comment.wizard"]
            .with_user(approver)
            .with_context(**wizard_ctx)
            .create(
                {
                    "comment": "Approved via test",
                    "validate_reject": "validate",
                    "res_model": wizard_ctx.get("default_res_model", "sale.order"),
                    "res_id": wizard_ctx.get("default_res_id", order.id),
                    "review_ids": [(6, 0, wizard_ctx.get("default_review_ids", []))],
                }
            )
        )
        wizard.add_comment()
        order.invalidate_model()

    # ------------------------------------------------------------------
    # XML tier definitions verification
    # ------------------------------------------------------------------
    def test_xml_tier_definitions_configured_correctly(self):
        """XML tier definitions use group review type with has_comment."""
        self.assertTrue(self.xml_tier_manager.active)
        self.assertTrue(self.xml_tier_director.active)
        self.assertEqual(self.xml_tier_manager.review_type, "group")
        self.assertTrue(self.xml_tier_manager.has_comment)
        self.assertEqual(self.xml_tier_director.review_type, "group")
        self.assertTrue(self.xml_tier_director.has_comment)

    # ------------------------------------------------------------------
    # 1. No violations → confirms directly
    # ------------------------------------------------------------------
    def test_no_violations_confirms_directly(self):
        """Order with no discount issues confirms without tier validation."""
        order = self._create_clean_order()
        self._create_order_line(order, self.product_a, qty=10)
        self.assertEqual(order.discount_approval_level, "none")
        # Should confirm without raising
        order.with_user(self.salesperson).action_confirm()
        self.assertEqual(order.state, "sale")

    # ------------------------------------------------------------------
    # 2. Extra discount within manager limit → needs manager
    # ------------------------------------------------------------------
    def test_extra_discount_within_manager_limit_needs_manager(self):
        """Extra discount <= manager_extra_limit requires manager approval."""
        order = self._create_clean_order()
        line = self._create_order_line(order, self.product_a, qty=10)
        # manager_extra_limit = 5, set extra_discount = 3 (within limit)
        line.write({"extra_discount": 3.0, "extra_discount_reason": "Test"})
        self.assertEqual(order.discount_approval_level, "manager")

    # ------------------------------------------------------------------
    # 3. Manager approves via comment wizard → can confirm
    # ------------------------------------------------------------------
    def test_manager_approves_then_confirms(self):
        """Full flow: manager approves via group tier + comment → order confirms."""
        order = self._create_clean_order()
        line = self._create_order_line(order, self.product_a, qty=10)
        line.write({"extra_discount": 3.0, "extra_discount_reason": "Test"})
        self.assertEqual(order.discount_approval_level, "manager")

        order.with_user(self.salesperson).request_validation()
        order.invalidate_model()
        self.assertTrue(order.review_ids)
        self.assertEqual(order.validation_status, "pending")

        self._approve_via_comment_wizard(order, self.manager_user)
        self.assertEqual(order.validation_status, "validated")

        # Verify comment was persisted on the review
        approved_review = order.review_ids.filtered(
            lambda review: review.status == "approved"
        )
        self.assertTrue(approved_review)
        self.assertEqual(approved_review.comment, "Approved via test")

        order.with_user(self.salesperson).action_confirm()
        self.assertEqual(order.state, "sale")

    # ------------------------------------------------------------------
    # 4. Extra discount above manager limit → needs director
    # ------------------------------------------------------------------
    def test_extra_discount_above_manager_limit_needs_director(self):
        """Extra discount > manager_extra_limit requires director approval."""
        order = self._create_clean_order()
        line = self._create_order_line(order, self.product_a, qty=10)
        # manager_extra_limit = 5, set extra_discount = 7 (above limit)
        line.write({"extra_discount": 7.0, "extra_discount_reason": "Test"})
        self.assertEqual(order.discount_approval_level, "director")

    # ------------------------------------------------------------------
    # 5. Cash discount above limit → needs director
    # ------------------------------------------------------------------
    def test_cash_above_limit_needs_director(self):
        """Cash discount above profile max requires director approval."""
        order = self._create_clean_order()
        self._create_order_line(order, self.product_a, qty=10)
        # cash_discount_max = 5, set cash_discount = 8
        order.write({"cash_discount": 8.0})
        self.assertEqual(order.discount_approval_level, "director")

    # ------------------------------------------------------------------
    # 6. FOB discount above limit → needs director
    # ------------------------------------------------------------------
    def test_fob_above_limit_needs_director(self):
        """FOB discount above profile max requires director approval."""
        order = self._create_clean_order()
        self._create_order_line(order, self.product_a, qty=10)
        # fob_discount_max = 3, set fob_discount = 5
        order.write({"fob_discount": 5.0})
        self.assertEqual(order.discount_approval_level, "director")

    # ------------------------------------------------------------------
    # 7. Payment term above limit → needs director
    # ------------------------------------------------------------------
    def test_payment_term_above_limit_needs_director(self):
        """Cash discount > 0 + long payment term requires director approval."""
        order = self._create_clean_order()
        self._create_order_line(order, self.product_a, qty=10)
        # cash_discount > 0 (default from condition = 2)
        # cash_term_avg_days_max = 30, payment_term_long = 60 days
        order.write(
            {
                "cash_discount": 2.0,
                "payment_term_id": self.payment_term_long.id,
            }
        )
        self.assertGreater(order.payment_term_avg_days, 30)
        self.assertEqual(order.discount_approval_level, "director")

    # ------------------------------------------------------------------
    # 8. Internal band violation → needs director
    # ------------------------------------------------------------------
    def test_internal_band_violation_needs_director(self):
        """Order below internal band minimum requires director approval."""
        self._setup_internal_policy()
        # Switch salesperson to internal profile so condition resolves it
        self.salesperson.partner_id.sales_profile_id = self.internal_profile
        self.env.company.default_sales_profile_id = self.internal_profile
        # Trigger recompute of applicable_profile_id on the condition
        self.condition.invalidate_recordset(["applicable_profile_id"])

        order = self._create_clean_order()
        self.assertEqual(
            order.sales_profile_id,
            self.internal_profile,
            "Order should use internal profile.",
        )
        # Product A = 100, qty = 1 → amount = 100
        # Band requires min 1000 for seller_discount 5%
        line = self._create_order_line(order, self.product_a, qty=1)
        line.write({"seller_discount": 5.0})

        self.assertLess(order.amount_untaxed, 1000)
        self.assertEqual(order.discount_approval_level, "director")

    # ------------------------------------------------------------------
    # 9. Mixed manager + director → uses director
    # ------------------------------------------------------------------
    def test_mixed_manager_and_director_uses_director(self):
        """When both manager and director violations exist, level = director."""
        order = self._create_clean_order()
        line = self._create_order_line(order, self.product_a, qty=10)
        # extra_discount = 3 (manager level) + cash above limit (director)
        line.write({"extra_discount": 3.0, "extra_discount_reason": "Test"})
        order.write({"cash_discount": 8.0})
        self.assertEqual(order.discount_approval_level, "director")

    # ------------------------------------------------------------------
    # 10. Director approves via comment wizard
    # ------------------------------------------------------------------
    def test_director_approval_via_comment_wizard(self):
        """Director approves via group tier + comment wizard."""
        order = self._create_clean_order()
        line = self._create_order_line(order, self.product_a, qty=10)
        line.write({"extra_discount": 7.0, "extra_discount_reason": "Test"})
        self.assertEqual(order.discount_approval_level, "director")

        order.with_user(self.salesperson).request_validation()
        order.invalidate_model()
        self.assertTrue(order.review_ids)

        self._approve_via_comment_wizard(order, self.director_user)
        self.assertEqual(order.validation_status, "validated")

    # ------------------------------------------------------------------
    # 11. Revalidation on cash_discount change
    # ------------------------------------------------------------------
    def test_revalidation_on_cash_change(self):
        """Changing cash_discount after approval clears reviews."""
        order = self._create_clean_order()
        self._create_order_line(order, self.product_a, qty=10)
        order.write({"cash_discount": 8.0})
        self.assertEqual(order.discount_approval_level, "director")

        order.with_user(self.salesperson).request_validation()
        order.invalidate_model()
        self._approve_via_comment_wizard(order, self.director_user)
        self.assertEqual(order.validation_status, "validated")

        # Change cash_discount → triggers revalidation
        order.write({"cash_discount": 9.0})
        order.invalidate_model()
        self.assertFalse(
            order.review_ids,
            "Reviews should be cleared after cash_discount change.",
        )

    # ------------------------------------------------------------------
    # 12. Revalidation on extra_discount change
    # ------------------------------------------------------------------
    def test_revalidation_on_extra_discount_change(self):
        """Changing extra_discount on line after approval clears reviews."""
        order = self._create_clean_order()
        line = self._create_order_line(order, self.product_a, qty=10)
        line.write({"extra_discount": 7.0, "extra_discount_reason": "Test"})
        self.assertEqual(order.discount_approval_level, "director")

        order.with_user(self.salesperson).request_validation()
        order.invalidate_model()
        self._approve_via_comment_wizard(order, self.director_user)
        self.assertEqual(order.validation_status, "validated")

        # Change extra_discount → triggers revalidation
        line.write({"extra_discount": 8.0, "extra_discount_reason": "Test"})
        self.assertFalse(
            order.review_ids,
            "Reviews should be cleared after extra_discount change.",
        )

    # ------------------------------------------------------------------
    # 13. Revalidation on seller_discount change
    # ------------------------------------------------------------------
    def test_revalidation_on_seller_discount_change(self):
        """Changing seller_discount on line after approval clears reviews."""
        self._setup_internal_policy()
        self.condition.applicable_profile_id = self.internal_profile
        order = self._create_clean_order()
        line = self._create_order_line(
            order,
            self.product_a,
            qty=1,
            seller_discount=4.0,
            base_price=500.0,
        )
        # Order amount ~500 < band minimum 1000 for 4% → director
        self.assertEqual(order.discount_approval_level, "director")

        order.with_user(self.salesperson).request_validation()
        order.invalidate_model()
        self._approve_via_comment_wizard(order, self.director_user)
        self.assertEqual(order.validation_status, "validated")

        # Change seller_discount → triggers revalidation via line write
        line.write({"seller_discount": 5.0})
        order.invalidate_model()
        self.assertFalse(
            order.review_ids,
            "Reviews should be cleared after seller_discount change.",
        )

    # ------------------------------------------------------------------
    # 14. Violation disappears → confirms without tier
    # ------------------------------------------------------------------
    def test_violation_disappears_confirms_without_tier(self):
        """Reducing discount removes violation, order confirms directly."""
        order = self._create_clean_order()
        self._create_order_line(order, self.product_a, qty=10)
        # Set cash above limit
        order.write({"cash_discount": 8.0})
        self.assertEqual(order.discount_approval_level, "director")

        order.with_user(self.salesperson).request_validation()
        order.invalidate_model()
        self.assertTrue(order.review_ids)

        # Reduce cash to within limit
        order.write({"cash_discount": 3.0})
        self.assertEqual(order.discount_approval_level, "none")
        # Reviews should be cleared by _sync_discount_validation_state
        # With level = none, no tier matches, so order is confirmable
        order.with_user(self.salesperson).action_confirm()
        self.assertEqual(order.state, "sale")

    # ------------------------------------------------------------------
    # 15. Snapshot contains violation messages
    # ------------------------------------------------------------------
    def test_snapshot_contains_violation_messages(self):
        """Snapshot text includes actual violation messages."""
        order = self._create_clean_order()
        line = self._create_order_line(order, self.product_a, qty=10)
        line.write({"extra_discount": 3.0, "extra_discount_reason": "Test"})
        order.write({"cash_discount": 8.0})

        order.with_user(self.salesperson).request_validation()
        order.invalidate_model()
        snapshot = order.discount_approval_snapshot
        self.assertTrue(snapshot, "Snapshot should not be empty.")
        # Cash discount violation
        self.assertIn("Cash discount", snapshot)
        self.assertIn("exceeds the maximum", snapshot)
        # Extra discount violation
        self.assertIn("extra discount", snapshot)
        self.assertIn("Product A", snapshot)

    # ------------------------------------------------------------------
    # 16. Snapshot regenerated after restart
    # ------------------------------------------------------------------
    def test_snapshot_regenerated_after_restart(self):
        """Snapshot is updated when discount changes after approval."""
        order = self._create_clean_order()
        self._create_order_line(order, self.product_a, qty=10)
        order.write({"cash_discount": 8.0})

        order.with_user(self.salesperson).request_validation()
        order.invalidate_model()
        snapshot_before = order.discount_approval_snapshot
        self.assertTrue(snapshot_before)

        # Change discount → triggers restart and snapshot regeneration
        order.write({"cash_discount": 9.0})
        snapshot_after = order.discount_approval_snapshot
        self.assertTrue(snapshot_after)
        self.assertNotEqual(
            snapshot_before,
            snapshot_after,
            "Snapshot should be regenerated with updated values.",
        )

    # ------------------------------------------------------------------
    # 17. Director auto-approves on confirm (formal flow, not bypass)
    # ------------------------------------------------------------------
    def test_director_auto_approves_on_confirm(self):
        """Director confirming triggers auto-approval via XML group tier."""
        order = self._create_clean_order()
        line = self._create_order_line(order, self.product_a, qty=10)
        line.write({"extra_discount": 7.0, "extra_discount_reason": "Test"})
        self.assertEqual(order.discount_approval_level, "director")

        # Director confirms directly — tier auto-approves
        order.with_user(self.director_user).action_confirm()
        self.assertEqual(order.state, "sale")
        # Verify the review was created and approved (formal flow, not bypass)
        approved = order.review_ids.filtered(lambda review: review.status == "approved")
        self.assertTrue(
            approved,
            "Director should have an approved review in the formal tier flow.",
        )

    # ------------------------------------------------------------------
    # 18. Manager cannot approve director-level tier
    # ------------------------------------------------------------------
    def test_manager_cannot_approve_director_tier(self):
        """Manager cannot validate a director-level review."""
        order = self._create_clean_order()
        line = self._create_order_line(order, self.product_a, qty=10)
        line.write({"extra_discount": 7.0, "extra_discount_reason": "Test"})
        self.assertEqual(order.discount_approval_level, "director")

        order.with_user(self.salesperson).request_validation()
        order.invalidate_model()
        self.assertEqual(order.validation_status, "pending")

        # Manager tries to validate — should not succeed because the tier
        # definition requires the director group, and manager is not in it.
        # With group tiers, validate_tier() returns without approving when
        # the user is not in the required group.
        order.with_user(self.manager_user).validate_tier()
        order.invalidate_model()

        # Review should still be pending (manager is not in director group)
        pending_reviews = order.review_ids.filtered(
            lambda review: review.status == "pending"
        )
        self.assertTrue(
            pending_reviews,
            "Director-level review should remain pending after manager attempt.",
        )
        self.assertNotEqual(order.validation_status, "validated")

    # ------------------------------------------------------------------
    # 19. Seller discount hard block (not tier, ValidationError)
    # ------------------------------------------------------------------
    def test_seller_discount_hard_block(self):
        """Seller discount above absolute max raises ValidationError."""
        # The general_rule has commission bands with discount_up_to = 5 and 10.
        # So absolute max seller_discount = 10 (highest band).
        order = self._create_clean_order()
        line = self._create_order_line(order, self.product_a, qty=10)
        # Try to set seller_discount above the absolute max (10)
        with self.assertRaises(ValidationError):
            line.write({"seller_discount": 12.0})

    # ------------------------------------------------------------------
    # 20. Confirm blocked while validation is pending
    # ------------------------------------------------------------------
    def test_confirm_blocked_while_pending(self):
        """Cannot confirm order while tier validation is pending."""
        order = self._create_clean_order()
        line = self._create_order_line(order, self.product_a, qty=10)
        line.write({"extra_discount": 3.0, "extra_discount_reason": "Test"})
        self.assertEqual(order.discount_approval_level, "manager")

        order.with_user(self.salesperson).request_validation()
        order.invalidate_model()
        self.assertEqual(order.validation_status, "pending")

        # Try to confirm before approval → blocked by tier.validation
        with self.assertRaises(ValidationError):
            order.with_user(self.salesperson).action_confirm()

    # ------------------------------------------------------------------
    # 21. Edit discount during pending validation → allowed, triggers restart
    # ------------------------------------------------------------------
    def test_edit_during_pending_validation(self):
        """Editing cash_discount while validation is pending restarts it."""
        order = self._create_clean_order()
        self._create_order_line(order, self.product_a, qty=10)
        order.write({"cash_discount": 8.0})
        self.assertEqual(order.discount_approval_level, "director")

        order.with_user(self.salesperson).request_validation()
        order.invalidate_model()
        self.assertEqual(order.validation_status, "pending")
        old_review_ids = order.review_ids.ids

        # Edit cash_discount — allowed via _get_under_validation_exceptions
        order.write({"cash_discount": 9.0})
        # Validation should have been restarted
        self.assertNotEqual(
            order.review_ids.ids,
            old_review_ids,
            "Review records should change after discount edit during pending.",
        )

    # ------------------------------------------------------------------
    # Coverage: _build_approval_snapshot with empty issues
    # ------------------------------------------------------------------
    def test_snapshot_returns_false_when_no_issues(self):
        """Snapshot returns False when there are no validation issues."""
        order = self._create_clean_order()
        self._create_order_line(order, self.product_a, qty=10)
        result = order._build_approval_snapshot([])
        self.assertFalse(result)

    # ------------------------------------------------------------------
    # Coverage: internal band skip when rule has no bands
    # ------------------------------------------------------------------
    def test_internal_no_bands_skips_band_check(self):
        """Internal profile with rule without bands skips band validation."""
        self._setup_internal_policy()
        # Remove bands and set a static seller_discount_max
        self.internal_general_rule.order_value_band_ids.unlink()
        self.internal_general_rule.seller_discount_max = 10.0
        self.condition.applicable_profile_id = self.internal_profile
        order = self._create_clean_order()
        self._create_order_line(
            order, self.product_a, qty=1, seller_discount=4.0, base_price=500.0
        )
        # No band → no band violation → level should be none
        self.assertEqual(order.discount_approval_level, "none")

    # ------------------------------------------------------------------
    # Coverage: payment_term_avg_days without payment term
    # ------------------------------------------------------------------
    def test_payment_term_avg_days_no_term(self):
        """Order without payment term has avg_days = 0."""
        order = self._create_clean_order()
        order.payment_term_id = False
        self.assertAlmostEqual(order.payment_term_avg_days, 0.0, places=2)

    # ------------------------------------------------------------------
    # Coverage: _notify_accepted_reviews_body with violations
    # ------------------------------------------------------------------
    def test_approval_chatter_includes_violations(self):
        """Approval chatter message includes the discount violation snapshot."""
        order = self._create_clean_order()
        self._create_order_line(order, self.product_a, qty=10)
        order.write({"cash_discount": 8.0})
        self.assertTrue(order.discount_approval_snapshot)

        order.with_user(self.salesperson).request_validation()
        order.invalidate_model()
        self._approve_via_comment_wizard(order, self.director_user)

        # Check that _notify_accepted_reviews_body includes violations
        body = order._notify_accepted_reviews_body()
        self.assertIn("policy exceptions", body)
        self.assertIn("Cash discount", body)

    def test_approval_chatter_without_violations(self):
        """Approval chatter message is standard when no violations exist."""
        order = self._create_clean_order()
        self._create_order_line(order, self.product_a, qty=10)
        # No violations — discount_approval_snapshot is False
        self.assertFalse(order.discount_approval_snapshot)
        body = order._notify_accepted_reviews_body()
        self.assertNotIn("policy exceptions", body)

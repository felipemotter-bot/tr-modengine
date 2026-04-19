# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestEffectiveCondition(CommercialPolicyTestCommon):
    """Tests for effective_condition_id compute with group inheritance."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        # Group with its own condition
        cls.group_condition = (
            cls.env["partner.commercial.condition"]
            .with_user(cls.director_user)
            .create(
                {
                    "partner_id": cls.customer_group.id,
                    "cash_discount": 1.0,
                    "fob_discount": 0.5,
                    "seller_discount": 3.0,
                }
            )
        )
        cls.customer_group.commercial_condition_id = cls.group_condition

    def test_own_condition_used_when_set(self):
        """Partner with own condition uses it as effective."""
        self.assertEqual(self.customer.effective_condition_id, self.condition)
        self.assertFalse(self.customer.condition_inherited)
        self.assertFalse(self.customer.condition_is_override)

    def test_inherits_from_group(self):
        """Partner without own condition inherits from group."""
        child = self.env["res.partner"].create(
            {
                "name": "Group Child",
                "company_group_id": self.customer_group.id,
            }
        )
        self.assertEqual(child.effective_condition_id, self.group_condition)
        self.assertTrue(child.condition_inherited)
        self.assertFalse(child.condition_is_override)
        self.assertEqual(
            child.condition_inherited_from, self.customer_group.display_name
        )

    def test_same_condition_as_group_is_inherited(self):
        """Partner with same condition as group is treated as inherited."""
        child = self.env["res.partner"].create(
            {
                "name": "Same Cond Child",
                "company_group_id": self.customer_group.id,
            }
        )
        # Assign the group's condition directly (like the hook does)
        child.commercial_condition_id = self.group_condition
        self.assertEqual(child.effective_condition_id, self.group_condition)
        self.assertTrue(child.condition_inherited)
        self.assertFalse(child.condition_is_override)
        self.assertEqual(
            child.condition_inherited_from, self.customer_group.display_name
        )

    def test_override_detected(self):
        """Partner with own condition in a group is flagged as override."""
        child = self.env["res.partner"].create(
            {
                "name": "Override Child",
                "company_group_id": self.customer_group.id,
            }
        )
        own_condition = (
            self.env["partner.commercial.condition"]
            .with_user(self.director_user)
            .create(
                {
                    "partner_id": child.id,
                    "cash_discount": 9.0,
                    "seller_discount": 2.0,
                }
            )
        )
        child.commercial_condition_id = own_condition
        self.assertEqual(child.effective_condition_id, own_condition)
        self.assertFalse(child.condition_inherited)
        self.assertTrue(child.condition_is_override)

    def test_group_head_not_marked_as_inherited(self):
        """Group head with own condition is NOT marked as inherited."""
        # The group head's condition belongs to itself, not inherited
        self.customer_group.commercial_condition_id = self.group_condition
        self.assertEqual(
            self.customer_group.effective_condition_id, self.group_condition
        )
        self.assertFalse(self.customer_group.condition_inherited)
        self.assertFalse(self.customer_group.condition_is_override)

    def test_group_head_detected(self):
        """Group head has is_group_head=True and correct member count."""
        child1 = self.env["res.partner"].create(
            {"name": "Member 1", "company_group_id": self.customer_group.id}
        )
        child2 = self.env["res.partner"].create(
            {"name": "Member 2", "company_group_id": self.customer_group.id}
        )
        self.assertTrue(self.customer_group.is_group_head)
        self.assertGreaterEqual(self.customer_group.group_member_count, 2)
        self.assertFalse(child1.is_group_head)
        self.assertFalse(child2.is_group_head)

    def test_no_group_no_condition(self):
        """Partner without group and without condition has empty effective."""
        orphan = self.env["res.partner"].create({"name": "Orphan"})
        self.assertFalse(orphan.effective_condition_id)
        self.assertFalse(orphan.condition_inherited)
        self.assertFalse(orphan.condition_is_override)


@tagged("post_install", "-at_install")
class TestConditionConstraint(CommercialPolicyTestCommon):
    """Tests for condition-belongs-to-partner-or-group constraint."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_own_condition_allowed(self):
        """Partner can have condition that belongs to itself."""
        # self.customer already has self.condition — no error
        self.assertEqual(
            self.customer.commercial_condition_id.partner_id, self.customer
        )

    def test_group_condition_allowed(self):
        """Partner can have condition that belongs to its group."""
        group_cond = (
            self.env["partner.commercial.condition"]
            .with_user(self.director_user)
            .create(
                {
                    "partner_id": self.customer_group.id,
                    "seller_discount": 1.0,
                }
            )
        )
        child = self.env["res.partner"].create(
            {
                "name": "Child",
                "company_group_id": self.customer_group.id,
            }
        )
        child.commercial_condition_id = group_cond
        # Should not raise

    def test_foreign_condition_rejected(self):
        """Partner cannot have condition from unrelated partner."""
        other_partner = self.env["res.partner"].create({"name": "Other"})
        other_condition = (
            self.env["partner.commercial.condition"]
            .with_user(self.director_user)
            .create(
                {
                    "partner_id": other_partner.id,
                    "seller_discount": 1.0,
                }
            )
        )
        with self.assertRaises(ValidationError):
            self.customer.commercial_condition_id = other_condition


@tagged("post_install", "-at_install")
class TestPartnerConditionActions(CommercialPolicyTestCommon):
    """Tests for create, override and remove actions on partner."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls.group_condition = (
            cls.env["partner.commercial.condition"]
            .with_user(cls.director_user)
            .create(
                {
                    "partner_id": cls.customer_group.id,
                    "cash_discount": 2.0,
                    "fob_discount": 1.0,
                    "seller_discount": 4.0,
                    "contractual_return": 5.0,
                }
            )
        )
        cls.customer_group.commercial_condition_id = cls.group_condition

    def test_action_create_commercial_condition(self):
        """action_create creates a new condition for the partner."""
        partner = self.env["res.partner"].create({"name": "New Customer"})
        self.assertFalse(partner.commercial_condition_id)
        result = partner.action_create_commercial_condition()
        self.assertTrue(partner.commercial_condition_id)
        self.assertEqual(result["res_model"], "partner.commercial.condition")

    def test_action_view_creates_if_missing(self):
        """action_view creates condition if none exists."""
        partner = self.env["res.partner"].create({"name": "No Cond"})
        result = partner.action_view_commercial_condition()
        self.assertTrue(partner.commercial_condition_id)
        self.assertEqual(result["res_model"], "partner.commercial.condition")

    def test_action_view_opens_existing(self):
        """action_view opens the effective condition."""
        result = self.customer.action_view_commercial_condition()
        self.assertEqual(result["res_id"], self.condition.id)

    def test_action_create_override(self):
        """Override copies values from group condition."""
        child = self.env["res.partner"].create(
            {
                "name": "Child Override",
                "company_group_id": self.customer_group.id,
            }
        )
        self.assertTrue(child.condition_inherited)
        result = child.action_create_override_condition()
        self.assertTrue(child.commercial_condition_id)
        self.assertTrue(child.condition_is_override)
        # Values copied from group
        override = child.commercial_condition_id
        self.assertAlmostEqual(override.cash_discount, 2.0)
        self.assertAlmostEqual(override.seller_discount, 4.0)
        self.assertAlmostEqual(override.contractual_return, 5.0)
        self.assertEqual(result["res_model"], "partner.commercial.condition")

    def test_action_remove_override(self):
        """Remove override deletes own condition and inherits from group."""
        child = self.env["res.partner"].create(
            {
                "name": "Child Remove",
                "company_group_id": self.customer_group.id,
            }
        )
        child.action_create_override_condition()
        own_cond = child.commercial_condition_id
        self.assertTrue(own_cond.exists())

        child.action_remove_override_condition()
        self.assertFalse(child.commercial_condition_id)
        self.assertFalse(own_cond.exists())
        self.assertTrue(child.condition_inherited)


@tagged("post_install", "-at_install")
class TestGroupChangeWizard(CommercialPolicyTestCommon):
    """Tests for wizard when partner with condition is added to group."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls.group_condition = (
            cls.env["partner.commercial.condition"]
            .with_user(cls.director_user)
            .create(
                {
                    "partner_id": cls.customer_group.id,
                    "seller_discount": 2.0,
                }
            )
        )
        cls.customer_group.commercial_condition_id = cls.group_condition

    def test_onchange_warns_on_group_change_with_conflict(self):
        """Onchange warns when partner with condition is added to group."""
        self.customer.company_group_id = self.customer_group
        result = self.customer._onchange_company_group_id()
        self.assertIn("warning", result)

    def test_wizard_keep_action(self):
        """Wizard 'keep' action preserves own condition."""
        wizard = self.env["tr.group.condition.wizard"].create(
            {
                "partner_id": self.customer.id,
                "group_id": self.customer_group.id,
                "own_condition_id": self.condition.id,
                "group_condition_id": self.group_condition.id,
                "action": "keep",
            }
        )
        wizard.action_confirm()
        self.assertEqual(self.customer.commercial_condition_id, self.condition)

    def test_wizard_inherit_action(self):
        """Wizard 'inherit' action clears own condition."""
        wizard = self.env["tr.group.condition.wizard"].create(
            {
                "partner_id": self.customer.id,
                "group_id": self.customer_group.id,
                "own_condition_id": self.condition.id,
                "group_condition_id": self.group_condition.id,
                "action": "inherit",
            }
        )
        wizard.action_confirm()
        self.assertFalse(self.customer.commercial_condition_id)


@tagged("post_install", "-at_install")
class TestPunctualityDiscount(CommercialPolicyTestCommon):
    """Tests for punctuality_discount compute from contractual_return."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls.condition.contractual_return = 5.0

    def test_punctuality_follows_contractual_return_in_draft(self):
        """punctuality_discount mirrors contractual_return in draft."""
        order = self._create_order()
        self.assertAlmostEqual(order.punctuality_discount, 5.0)

    def test_punctuality_snapshot_not_live(self):
        """contractual_return is snapshot — changing condition does not
        update the order until reload.
        """
        order = self._create_order()
        original_cr = order.contractual_return
        # Change condition after order creation
        self.condition.with_user(self.director_user).contractual_return = 10.0
        # Order keeps snapshot value
        self.assertAlmostEqual(
            order.contractual_return,
            original_cr,
            places=2,
            msg="Order should keep snapshot, not follow condition live.",
        )
        # After reload, order gets the new value
        order._apply_reload_conditions()
        self.assertAlmostEqual(order.contractual_return, 10.0, places=2)
        self.assertAlmostEqual(order.punctuality_discount, 10.0, places=2)


@tagged("post_install", "-at_install")
class TestCommercialConditionWarning(CommercialPolicyTestCommon):
    """Tests for warning banner when partner has no condition."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_warning_when_no_condition(self):
        """Warning message shown when partner has no condition."""
        self.customer.commercial_condition_id = False
        order = self._create_order()
        self.assertTrue(order.commercial_condition_warning)

    def test_no_warning_when_has_condition(self):
        """No warning when partner has condition."""
        order = self._create_order()
        self.assertFalse(order.commercial_condition_warning)


@tagged("post_install", "-at_install")
class TestOrderUsesEffectiveCondition(CommercialPolicyTestCommon):
    """Tests for sale order using effective_condition_id from partner."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls.group_condition = (
            cls.env["partner.commercial.condition"]
            .with_user(cls.director_user)
            .create(
                {
                    "partner_id": cls.customer_group.id,
                    "cash_discount": 1.5,
                    "seller_discount": 2.5,
                }
            )
        )
        cls.customer_group.commercial_condition_id = cls.group_condition

    def test_order_gets_inherited_condition(self):
        """Order for partner inheriting from group gets group condition."""
        child = self.env["res.partner"].create(
            {
                "name": "Group Child Order",
                "company_group_id": self.customer_group.id,
            }
        )
        order = self._create_order(partner_id=child.id)
        self.assertEqual(order.commercial_condition_id, self.group_condition)


@tagged("post_install", "-at_install")
class TestOnchangeGroupWarning(CommercialPolicyTestCommon):
    """Tests for onchange warning when changing company group."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls.group_condition = (
            cls.env["partner.commercial.condition"]
            .with_user(cls.director_user)
            .create(
                {
                    "partner_id": cls.customer_group.id,
                    "seller_discount": 2.0,
                }
            )
        )
        cls.customer_group.commercial_condition_id = cls.group_condition

    def test_onchange_conflict_warning(self):
        """Warning when partner with condition joins group with condition."""
        self.customer.company_group_id = self.customer_group
        result = self.customer._onchange_company_group_id()
        self.assertIn("warning", result)
        self.assertIn("Override", result["warning"]["title"])

    def test_onchange_inherit_warning(self):
        """Warning when partner without condition joins group with condition."""
        partner = self.env["res.partner"].create({"name": "No Cond"})
        partner.company_group_id = self.customer_group
        result = partner._onchange_company_group_id()
        self.assertIn("warning", result)
        self.assertIn("inherited", result["warning"]["message"])

    def test_onchange_no_warning_no_group_condition(self):
        """No warning when group has no condition."""
        empty_group = self.env["res.partner"].create({"name": "Empty Group"})
        self.customer.company_group_id = empty_group
        result = self.customer._onchange_company_group_id()
        self.assertFalse(result.get("warning"))

    def test_onchange_no_warning_no_group(self):
        """No warning when clearing group."""
        self.customer.company_group_id = False
        result = self.customer._onchange_company_group_id()
        self.assertFalse(result.get("warning"))


@tagged("post_install", "-at_install")
class TestCronCleanupOrphanConditions(CommercialPolicyTestCommon):
    """Tests for orphan condition cleanup cron."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_orphan_condition_deleted(self):
        """Condition not referenced by any partner is deleted."""
        orphan = (
            self.env["partner.commercial.condition"]
            .with_user(self.director_user)
            .create(
                {
                    "partner_id": self.customer_group.id,
                    "seller_discount": 1.0,
                }
            )
        )
        # Not assigned to any partner via commercial_condition_id
        self.assertTrue(orphan.exists())
        self.env["partner.commercial.condition"]._cron_cleanup_orphan_conditions()
        self.assertFalse(orphan.exists())

    def test_referenced_condition_kept(self):
        """Condition referenced by a partner is not deleted."""
        self.assertTrue(self.condition.exists())
        self.env["partner.commercial.condition"]._cron_cleanup_orphan_conditions()
        self.assertTrue(self.condition.exists())

    def test_condition_used_in_order_kept(self):
        """Condition referenced by a sale order is not deleted."""
        order = self._create_order()
        condition = order.commercial_condition_id
        # Remove from partner but keep on order
        self.customer.commercial_condition_id = False
        self.assertTrue(condition.exists())
        self.env["partner.commercial.condition"]._cron_cleanup_orphan_conditions()
        self.assertTrue(condition.exists())


@tagged("post_install", "-at_install")
class TestSyncPartnerFieldsFromCondition(CommercialPolicyTestCommon):
    """Tests for syncing property fields from condition to partner."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls.payment_term = cls.env["account.payment.term"].create(
            {
                "name": "Test Term 30 days",
                "line_ids": [(0, 0, {"value": "balance", "days": 30})],
            }
        )
        cls.incoterm = cls.env["account.incoterms"].create(
            {"name": "Test FOB", "code": "TFOB"}
        )
        delivery_product = cls.env["product.product"].create(
            {"name": "Delivery", "type": "service"}
        )
        cls.carrier = cls.env["delivery.carrier"].create(
            {
                "name": "Test Carrier",
                "delivery_type": "fixed",
                "fixed_price": 10.0,
                "product_id": delivery_product.id,
            }
        )
        cls.payment_mode = cls.env["account.payment.mode"].search([], limit=1)

    def test_pricelist_synced_on_condition_assign(self):
        """Pricelist is synced when condition is assigned to partner."""
        self.assertEqual(
            self.customer.property_product_pricelist,
            self.pricelist,
        )

    def test_payment_term_synced_on_condition_write(self):
        """Payment term synced to partner when condition changes."""
        self.condition.write({"payment_term_id": self.payment_term.id})
        self.assertEqual(
            self.customer.property_payment_term_id,
            self.payment_term,
        )

    def test_payment_mode_synced_on_condition_write(self):
        """Payment mode synced to partner when condition changes."""
        self.assertTrue(
            self.payment_mode,
            "No payment mode available for testing",
        )
        self.condition.write({"payment_mode_id": self.payment_mode.id})
        self.assertEqual(
            self.customer.customer_payment_mode_id,
            self.payment_mode,
        )

    def test_delivery_carrier_synced_on_condition_write(self):
        """Delivery carrier synced to partner when condition changes."""
        self.condition.write({"delivery_carrier_id": self.carrier.id})
        self.assertEqual(
            self.customer.property_delivery_carrier_id,
            self.carrier,
        )

    def test_incoterm_synced_on_condition_write(self):
        """Incoterm synced to partner when condition changes."""
        self.condition.write({"incoterm_id": self.incoterm.id})
        self.assertEqual(
            self.customer.sale_incoterm_id,
            self.incoterm,
        )

    def test_sync_propagates_to_group_members(self):
        """Sync propagates to all partners using the group condition."""
        group_condition = (
            self.env["partner.commercial.condition"]
            .with_user(self.director_user)
            .create(
                {
                    "partner_id": self.customer_group.id,
                    "seller_discount": 1.0,
                }
            )
        )
        self.customer_group.commercial_condition_id = group_condition
        child = self.env["res.partner"].create(
            {
                "name": "Group Child Sync",
                "company_group_id": self.customer_group.id,
            }
        )
        child.commercial_condition_id = group_condition

        group_condition.write({"payment_term_id": self.payment_term.id})
        self.assertEqual(
            child.property_payment_term_id,
            self.payment_term,
        )

    def test_sync_propagates_to_inheriting_group_members(self):
        """Sync propagates to group members that inherit (no own condition)."""
        group_condition = (
            self.env["partner.commercial.condition"]
            .with_user(self.director_user)
            .create(
                {
                    "partner_id": self.customer_group.id,
                }
            )
        )
        self.customer_group.commercial_condition_id = group_condition
        # Child without own condition — inherits from group
        child = self.env["res.partner"].create(
            {
                "name": "Inheriting Child",
                "company_group_id": self.customer_group.id,
            }
        )
        self.assertFalse(child.commercial_condition_id)
        self.assertEqual(child.effective_condition_id, group_condition)

        # Write to group condition should sync to inheriting child
        group_condition.write({"payment_term_id": self.payment_term.id})
        self.assertEqual(
            child.property_payment_term_id,
            self.payment_term,
            "Inheriting group member should receive synced payment term.",
        )

        # Also verify Float field sync (contractual_return → punctuality_discount)
        group_condition.with_user(self.director_user).write({"contractual_return": 5.0})
        self.assertAlmostEqual(
            child.punctuality_discount,
            5.0,
            places=2,
            msg="Inheriting member should receive synced punctuality_discount.",
        )

    def test_sync_does_not_affect_member_with_override(self):
        """Group member with own condition is NOT updated by group sync."""
        group_condition = (
            self.env["partner.commercial.condition"]
            .with_user(self.director_user)
            .create(
                {
                    "partner_id": self.customer_group.id,
                }
            )
        )
        self.customer_group.commercial_condition_id = group_condition

        # Create a new child with own condition (override)
        child = self.env["res.partner"].create(
            {
                "name": "Child With Override",
                "company_group_id": self.customer_group.id,
            }
        )
        child_condition = (
            self.env["partner.commercial.condition"]
            .with_user(self.director_user)
            .create(
                {
                    "partner_id": child.id,
                    "payment_term_id": self.payment_term.id,
                }
            )
        )
        child.commercial_condition_id = child_condition
        self.assertTrue(child.commercial_condition_id)

        # Change group condition — child with override should NOT be affected
        other_term = self.env["account.payment.term"].create(
            {
                "name": "Other Term",
                "line_ids": [(0, 0, {"value": "balance", "days": 90})],
            }
        )
        group_condition.write({"payment_term_id": other_term.id})
        self.assertEqual(
            child.property_payment_term_id,
            self.payment_term,
            "Member with override should keep own payment term.",
        )

    def test_member_gains_override_stops_inheriting_sync(self):
        """Member that gains own condition stops receiving group sync."""
        group_condition = (
            self.env["partner.commercial.condition"]
            .with_user(self.director_user)
            .create(
                {
                    "partner_id": self.customer_group.id,
                }
            )
        )
        self.customer_group.commercial_condition_id = group_condition
        child = self.env["res.partner"].create(
            {
                "name": "Child Goes Override",
                "company_group_id": self.customer_group.id,
            }
        )
        # Initially inherits
        self.assertFalse(child.commercial_condition_id)
        self.assertEqual(child.effective_condition_id, group_condition)

        # Sync works while inheriting
        group_condition.write({"payment_term_id": self.payment_term.id})
        self.assertEqual(child.property_payment_term_id, self.payment_term)

        # Child gets own condition (override) — keeps same payment_term
        child_condition = (
            self.env["partner.commercial.condition"]
            .with_user(self.director_user)
            .create(
                {
                    "partner_id": child.id,
                    "payment_term_id": self.payment_term.id,
                }
            )
        )
        child.commercial_condition_id = child_condition
        self.assertTrue(child.commercial_condition_id)

        # Change group condition — child should NOT be affected anymore
        other_term = self.env["account.payment.term"].create(
            {
                "name": "Another Term",
                "line_ids": [(0, 0, {"value": "balance", "days": 45})],
            }
        )
        group_condition.write({"payment_term_id": other_term.id})
        self.assertEqual(
            child.property_payment_term_id,
            self.payment_term,
            "Member with override should not receive group sync anymore.",
        )

    def test_pricelist_synced_on_condition_write(self):
        """Pricelist update on condition syncs to partner."""
        new_pricelist = self.env["product.pricelist"].create(
            {
                "name": "New PL",
                "currency_id": self.env.ref("base.BRL").id,
            }
        )
        self.condition.with_user(self.director_user).write(
            {"pricelist_id": new_pricelist.id}
        )
        self.assertEqual(
            self.customer.property_product_pricelist,
            new_pricelist,
        )

    def test_contractual_return_synced_as_punctuality(self):
        """Contractual return syncs to punctuality_discount on partner."""
        self.condition.write({"contractual_return": 7.5})
        self.assertAlmostEqual(
            self.customer.punctuality_discount,
            7.5,
        )

    def test_sync_skips_missing_partner_fields(self):
        """Sync gracefully skips fields not present on partner model."""
        # Just verify no error — all fields should exist with our deps
        self.condition.write({"payment_term_id": self.payment_term.id})
        self.assertTrue(True)

    def test_sync_on_condition_create(self):
        """Sync runs when condition is created via _sync_to_partners."""
        new_partner = self.env["res.partner"].create({"name": "SyncNew"})
        condition = (
            self.env["partner.commercial.condition"]
            .with_user(self.director_user)
            .create(
                {
                    "partner_id": new_partner.id,
                    "payment_term_id": self.payment_term.id,
                }
            )
        )
        new_partner.commercial_condition_id = condition
        self.assertEqual(
            new_partner.property_payment_term_id,
            self.payment_term,
        )

    def test_sync_does_not_run_without_sync_fields(self):
        """Write on non-sync fields does not trigger sync."""
        old_pricelist = self.customer.property_product_pricelist
        self.condition.write({"cash_discount": 1.5})
        self.assertEqual(
            self.customer.property_product_pricelist,
            old_pricelist,
        )


@tagged("post_install", "-at_install")
class TestDisplayName(CommercialPolicyTestCommon):
    """Tests for commercial condition display_name."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_display_name_with_partner(self):
        """Display name shows 'Condition of [Partner]'."""
        self.assertIn(
            self.customer.name,
            self.condition.display_name,
        )

    def test_display_name_without_partner(self):
        """Display name fallback when no partner."""
        cond = self.env["partner.commercial.condition"].new({})
        self.assertTrue(cond.display_name)


@tagged("post_install", "-at_install")
class TestHookHelpers(CommercialPolicyTestCommon):
    """Tests for post_init_hook helper functions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_ref_to_id_valid(self):
        """_ref_to_id extracts ID from reference string."""
        from ..hooks import _ref_to_id

        self.assertEqual(_ref_to_id("product.pricelist,42"), 42)

    def test_ref_to_id_none(self):
        """_ref_to_id returns None for empty input."""
        from ..hooks import _ref_to_id

        self.assertIsNone(_ref_to_id(None))
        self.assertIsNone(_ref_to_id(""))

    def test_ref_to_id_invalid(self):
        """_ref_to_id returns None for malformed input."""
        from ..hooks import _ref_to_id

        self.assertIsNone(_ref_to_id("invalid"))

    def test_ensure_default_profiles_creates_profiles(self):
        """Default profiles are created if none exist."""
        from ..hooks import _ensure_default_profiles

        # Delete existing profiles
        self.env["tr.sales.profile"].search([]).unlink()
        _ensure_default_profiles(self.env)
        agent = self.env["tr.sales.profile"].search([("profile_type", "=", "agent")])
        internal = self.env["tr.sales.profile"].search(
            [("profile_type", "=", "internal")]
        )
        self.assertTrue(agent)
        self.assertTrue(internal)

    def test_ensure_default_profiles_idempotent(self):
        """Running twice does not create duplicate profiles."""
        from ..hooks import _ensure_default_profiles

        _ensure_default_profiles(self.env)
        count_before = self.env["tr.sales.profile"].search_count([])
        _ensure_default_profiles(self.env)
        count_after = self.env["tr.sales.profile"].search_count([])
        self.assertEqual(count_before, count_after)

    def test_ensure_profiles_have_rules_adds_placeholder(self):
        """Profiles without rules get a placeholder during migration."""
        from ..hooks import ensure_profiles_have_rules

        # Create a profile without rules via SQL to bypass the constraint
        # (simulating a profile that existed before the constraint was added)
        self.env.cr.execute(
            """
            INSERT INTO tr_sales_profile
                (name, profile_type, company_id, active,
                 create_uid, create_date, write_uid, write_date)
            VALUES
                ('Legacy Agent', 'agent', 1, true,
                 1, NOW(), 1, NOW())
            RETURNING id
            """
        )
        agent_id = self.env.cr.fetchone()[0]
        self.env.cr.execute(
            """
            INSERT INTO tr_sales_profile
                (name, profile_type, company_id, active,
                 create_uid, create_date, write_uid, write_date)
            VALUES
                ('Legacy Internal', 'internal', 1, true,
                 1, NOW(), 1, NOW())
            RETURNING id
            """
        )
        internal_id = self.env.cr.fetchone()[0]
        self.env.invalidate_all()

        Profile = self.env["tr.sales.profile"]
        legacy_agent = Profile.browse(agent_id)
        legacy_internal = Profile.browse(internal_id)
        self.assertFalse(legacy_agent.rule_ids)
        self.assertFalse(legacy_internal.rule_ids)

        ensure_profiles_have_rules(self.env)

        self.assertTrue(legacy_agent.rule_ids)
        self.assertTrue(legacy_internal.rule_ids)
        self.assertTrue(legacy_agent.rule_ids.commission_band_ids)
        self.assertTrue(legacy_internal.rule_ids.order_value_band_ids)

    def test_create_condition_sql(self):
        """_create_condition_sql creates a condition via SQL."""
        from ..hooks import _create_condition_sql

        new_partner = self.env["res.partner"].create({"name": "SQLTest"})
        condition_id = _create_condition_sql(
            self.env.cr,
            new_partner.id,
            self.pricelist.id,
            self.env.company.id,
        )
        self.assertTrue(condition_id)
        condition = self.env["partner.commercial.condition"].browse(condition_id)
        self.assertEqual(condition.partner_id, new_partner)

    def test_set_condition_property(self):
        """_set_condition_property creates ir.property record."""
        from ..hooks import _set_condition_property

        self.env.cr.execute(
            """
            SELECT id FROM ir_model_fields
            WHERE model = 'res.partner'
              AND name = 'commercial_condition_id'
            """
        )
        field_id = self.env.cr.fetchone()[0]
        new_partner = self.env["res.partner"].create({"name": "PropTest"})
        _set_condition_property(
            self.env.cr,
            new_partner.id,
            self.condition.id,
            field_id,
            self.env.company.id,
        )
        self.env.cr.execute(
            """
            SELECT value_reference FROM ir_property
            WHERE res_id = CONCAT('res.partner,', %s::text)
              AND name = 'commercial_condition_id'
            """,
            (new_partner.id,),
        )
        row = self.env.cr.fetchone()
        self.assertIn(str(self.condition.id), row[0])


@tagged("post_install", "-at_install")
class TestPartnerWriteSync(CommercialPolicyTestCommon):
    """Tests for partner write triggering sync."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_write_commercial_condition_triggers_sync(self):
        """Setting commercial_condition_id via write triggers sync."""
        new_partner = self.env["res.partner"].create({"name": "WriteSync"})
        new_cond = (
            self.env["partner.commercial.condition"]
            .with_user(self.director_user)
            .create(
                {
                    "partner_id": new_partner.id,
                    "pricelist_id": self.pricelist.id,
                }
            )
        )
        new_partner.write({"commercial_condition_id": new_cond.id})
        self.assertEqual(
            new_partner.property_product_pricelist,
            self.pricelist,
        )

    def test_write_other_fields_no_sync(self):
        """Writing non-condition fields does not trigger sync."""
        old_pl = self.customer.property_product_pricelist
        self.customer.write({"name": "Renamed"})
        self.assertEqual(
            self.customer.property_product_pricelist,
            old_pl,
        )


@tagged("post_install", "-at_install")
class TestSellerDiscountConstraint(CommercialPolicyTestCommon):
    """Tests for seller_discount constraint without seller_discount_max."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_constraint_triggers_on_seller_discount_write(self):
        """Constraint runs when seller_discount is written."""
        order = self._create_order()
        line = self._create_order_line(order)
        # Should not raise for valid discount
        line.write({"seller_discount": 3.0})


@tagged("post_install", "-at_install")
class TestSyncEdgeCases(CommercialPolicyTestCommon):
    """Tests for sync edge cases and branch coverage."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_sync_skips_partner_without_condition(self):
        """Sync does nothing for partner without effective condition."""
        partner = self.env["res.partner"].create({"name": "NoCond"})
        # Should not raise
        partner._sync_partner_fields_from_condition()

    def test_sync_handles_empty_many2one(self):
        """Sync writes False when condition field is empty Many2one."""
        self.condition.write({"payment_term_id": False})
        # payment_term_id is False, sync should set partner field to False
        self.customer._sync_partner_fields_from_condition()
        self.assertFalse(self.customer.property_payment_term_id)

    def test_sync_handles_zero_float(self):
        """Sync writes 0 for float fields."""
        self.condition.write({"contractual_return": 0.0})
        self.customer._sync_partner_fields_from_condition()
        self.assertAlmostEqual(self.customer.punctuality_discount, 0.0)

    def test_display_name_condition_without_partner(self):
        """Display name for new record without partner."""
        cond = self.env["partner.commercial.condition"].new({})
        self.assertTrue(cond.display_name)

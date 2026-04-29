# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestSalespersonConditionAccess(CommercialPolicyTestCommon):
    """ACL + business-logic tests for salesperson on commercial conditions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_salesperson_creates_condition_within_limits(self):
        """Salesperson can create a commercial condition within profile limits."""
        new_customer = self.env["res.partner"].create({"name": "SP Create Customer"})
        condition = (
            self.env["partner.commercial.condition"]
            .with_user(self.salesperson)
            .create(
                {
                    "partner_id": new_customer.id,
                    "cash_discount": 3.0,
                    "fob_discount": 2.0,
                    "seller_discount": 4.0,
                }
            )
        )
        self.assertTrue(condition.id)

    def test_salesperson_creates_condition_above_limit_blocked(self):
        """Salesperson cannot create condition with cash_discount above profile max."""
        new_customer = self.env["res.partner"].create(
            {"name": "SP Create Blocked Customer"}
        )
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition"].with_user(self.salesperson).create(
                {
                    "partner_id": new_customer.id,
                    "cash_discount": 99.0,
                }
            )

    def test_salesperson_writes_condition_within_limits(self):
        """Salesperson can edit commercial condition within profile limits."""
        self.condition.with_user(self.salesperson).write({"cash_discount": 4.0})
        self.assertAlmostEqual(self.condition.cash_discount, 4.0, places=2)

    def test_salesperson_writes_condition_above_limit_blocked(self):
        """Salesperson cannot set fob_discount above profile max."""
        with self.assertRaises(ValidationError):
            self.condition.with_user(self.salesperson).write({"fob_discount": 99.0})

    def test_salesperson_cannot_delete_condition(self):
        """Salesperson cannot delete a commercial condition (ACL)."""
        with self.assertRaises(AccessError):
            self.condition.with_user(self.salesperson).unlink()


@tagged("post_install", "-at_install")
class TestSalespersonConditionLineAccess(CommercialPolicyTestCommon):
    """ACL + business-logic tests for salesperson on condition lines."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_salesperson_creates_line_within_limits(self):
        """Salesperson can create a condition line within profile limits.

        Profile is resolved from the condition's partner (customer → no agent
        → company default = agent_profile).  General rule max = 10.
        """
        line = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.salesperson)
            .create(
                {
                    "condition_id": self.condition.id,
                    "product_tmpl_id": self.product_template_b.id,
                    "seller_discount": 4.0,
                }
            )
        )
        self.assertTrue(line.id)

    def test_salesperson_creates_line_above_limit_blocked(self):
        """Salesperson cannot create line with seller_discount above profile max.

        Profile is resolved from the condition's partner (company default =
        agent_profile).  General rule max = 10.
        """
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition.line"].with_user(
                self.salesperson
            ).create(
                {
                    "condition_id": self.condition.id,
                    "product_tmpl_id": self.product_template_b.id,
                    "seller_discount": 99.0,
                }
            )

    def test_salesperson_cannot_set_extra_discount(self):
        """Salesperson cannot set extra_discount > 0 on condition line."""
        with self.assertRaises(AccessError):
            self.env["partner.commercial.condition.line"].with_user(
                self.salesperson
            ).create(
                {
                    "condition_id": self.condition.id,
                    "product_tmpl_id": self.product_template_b.id,
                    "seller_discount": 2.0,
                    "extra_discount": 1.0,
                }
            )

    def test_salesperson_can_delete_line(self):
        """Salesperson can delete a condition line."""
        line = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.director_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "product_tmpl_id": self.product_template_b.id,
                    "seller_discount": 2.0,
                }
            )
        )
        line.with_user(self.salesperson).unlink()
        self.assertFalse(line.exists())


@tagged("post_install", "-at_install")
class TestManagerConditionAccess(CommercialPolicyTestCommon):
    """ACL + business-logic tests for manager on commercial conditions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls.manager_user.partner_id.sales_profile_id = cls.agent_profile

    def test_manager_writes_condition_within_limits(self):
        """Manager can edit commercial condition within profile limits."""
        self.condition.with_user(self.manager_user).write({"cash_discount": 4.0})
        self.assertAlmostEqual(self.condition.cash_discount, 4.0, places=2)

    def test_manager_writes_condition_above_limit_blocked(self):
        """Manager is also subject to profile limits."""
        with self.assertRaises(ValidationError):
            self.condition.with_user(self.manager_user).write({"cash_discount": 99.0})

    def test_manager_can_set_extra_discount_on_line(self):
        """Manager can set extra_discount > 0 on condition line."""
        line = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.manager_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "product_tmpl_id": self.product_template_b.id,
                    "seller_discount": 2.0,
                    "extra_discount": 1.0,
                }
            )
        )
        self.assertAlmostEqual(line.extra_discount, 1.0, places=2)

    def test_manager_can_delete_condition(self):
        """Manager can delete a commercial condition."""
        new_customer = self.env["res.partner"].create(
            {"name": "Manager Delete Customer"}
        )
        condition = (
            self.env["partner.commercial.condition"]
            .with_user(self.director_user)
            .create({"partner_id": new_customer.id})
        )
        condition.with_user(self.manager_user).unlink()
        self.assertFalse(condition.exists())


@tagged("post_install", "-at_install")
class TestDirectorConditionAccess(CommercialPolicyTestCommon):
    """ACL + business-logic tests for director on commercial conditions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_director_bypasses_all_limits(self):
        """Director can set any discount value, bypassing profile validation."""
        self.condition.with_user(self.director_user).write(
            {
                "cash_discount": 99.0,
                "fob_discount": 99.0,
                "seller_discount": 99.0,
            }
        )
        self.assertAlmostEqual(self.condition.cash_discount, 99.0, places=2)
        self.assertAlmostEqual(self.condition.fob_discount, 99.0, places=2)
        self.assertAlmostEqual(self.condition.seller_discount, 99.0, places=2)


@tagged("post_install", "-at_install")
class TestSalespersonProfileAccess(CommercialPolicyTestCommon):
    """ACL tests for salesperson on sales profile models (read-only)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    def test_salesperson_can_read_profile(self):
        """Salesperson can read sales profiles."""
        profile = self.agent_profile.with_user(self.salesperson)
        self.assertEqual(profile.name, "Agent Profile")

    def test_salesperson_cannot_write_profile(self):
        """Salesperson cannot edit sales profiles (ACL)."""
        with self.assertRaises(AccessError):
            self.agent_profile.with_user(self.salesperson).write(
                {"cash_discount_max": 99.0}
            )

    def test_salesperson_cannot_create_profile(self):
        """Salesperson cannot create sales profiles (ACL)."""
        with self.assertRaises(AccessError):
            self.env["tr.sales.profile"].with_user(self.salesperson).create(
                {
                    "name": "Unauthorized Profile",
                    "profile_type": "agent",
                    "cash_discount_max": 10.0,
                    "fob_discount_max": 5.0,
                    "cash_term_avg_days_max": 30,
                }
            )

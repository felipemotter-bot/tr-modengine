# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestGap1DynamicSellerDiscountMax(CommercialPolicyTestCommon):
    """Gap 1 — seller_discount_max dynamic by order value for internals."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_internal_policy()
        # Profile now resolves from condition (agent → company default).
        cls.env.company.default_sales_profile_id = cls.internal_profile
        # Clear salesperson's agent profile so _check_internal_profile_source
        # falls through to company default (which matches internal_profile).
        cls.salesperson.partner_id.sales_profile_id = False

    def test_internal_discount_max_from_band(self):
        """seller_discount_max returns band matching order amount_untaxed."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        # Order amount ~100, bands: 1000->5%, 5000->10%, 10000->15%
        # Amount < 1000 → no applicable band → 0%
        self.assertAlmostEqual(line.seller_discount_max, 0.0, places=2)

    def test_internal_discount_max_first_band(self):
        """seller_discount_max returns first band when amount >= min."""
        order = self._create_order()
        # Create lines to push amount_untaxed above 1000
        self._create_order_line(order, base_price=1100.0)
        line = self._create_order_line(order, base_price=100.0)
        # amount_untaxed should be >= 1000 → band 5%
        self.assertAlmostEqual(line.seller_discount_max, 5.0, places=2)

    def test_internal_discount_max_higher_band(self):
        """seller_discount_max returns higher band for larger orders."""
        order = self._create_order()
        self._create_order_line(order, base_price=6000.0)
        line = self._create_order_line(order, base_price=100.0)
        # amount_untaxed >= 5000 → band 10%
        self.assertAlmostEqual(line.seller_discount_max, 10.0, places=2)

    def test_agent_profile_uses_static_max(self):
        """Agent profiles still use the static seller_discount_max."""
        # Switch condition to resolve to agent_profile via company default
        self.env.company.default_sales_profile_id = self.agent_profile
        order = self._create_order()
        self._create_order_line(order, base_price=100.0)
        line = self._create_order_line(order, base_price=100.0)
        # general_rule has commission bands up to 10% → max=10.0
        self.assertAlmostEqual(line.seller_discount_max, 10.0, places=2)


@tagged("post_install", "-at_install")
class TestGap2ConditionValidation(CommercialPolicyTestCommon):
    """Gap 2 — Validation when writing to commercial conditions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_internal_policy()
        # Profile now resolves from condition's partner (agent → company default).
        # Set company default to internal profile so validation uses order value bands.
        cls.env.company.default_sales_profile_id = cls.internal_profile
        cls.salesperson.groups_id = [
            (4, cls.env.ref("sales_team.group_sale_salesman").id),
            (
                4,
                cls.env.ref("tr_commercial_policy.group_sales_manager").id,
            ),
        ]

    def test_no_profile_blocks_condition_write(self):
        """User without profile cannot edit condition discounts."""
        # Clear company default so profile can't resolve via fallback
        self.env.company.default_sales_profile_id = False
        # Also remove agent from customer so profile can't resolve from agent
        self.customer.agent_ids = [(5,)]
        user_no_profile = self.env["res.users"].create(
            {
                "name": "No Profile User",
                "login": "no_profile_user_tcp",
                "groups_id": [
                    (4, self.env.ref("sales_team.group_sale_salesman").id),
                    (
                        4,
                        self.env.ref("tr_commercial_policy.group_sales_manager").id,
                    ),
                ],
            }
        )
        with self.assertRaises(AccessError):
            self.condition.with_user(user_no_profile).write({"cash_discount": 1.0})

    def test_cash_discount_within_limit_ok(self):
        """Cash discount within profile limit is accepted."""
        # internal_profile.cash_discount_max = 8.0
        self.condition.with_user(self.salesperson).write({"cash_discount": 7.0})
        self.assertAlmostEqual(self.condition.cash_discount, 7.0, places=2)

    def test_cash_discount_exceeds_limit_blocked(self):
        """Cash discount exceeding profile limit is blocked."""
        with self.assertRaises(ValidationError):
            self.condition.with_user(self.salesperson).write({"cash_discount": 9.0})

    def test_fob_discount_exceeds_limit_blocked(self):
        """FOB discount exceeding profile limit is blocked."""
        # internal_profile.fob_discount_max = 5.0
        with self.assertRaises(ValidationError):
            self.condition.with_user(self.salesperson).write({"fob_discount": 6.0})

    def test_director_bypasses_all_limits(self):
        """Director can write any discount value."""
        self.condition.with_user(self.director_user).write(
            {"cash_discount": 99.0, "fob_discount": 99.0}
        )
        self.assertAlmostEqual(self.condition.cash_discount, 99.0, places=2)

    def test_non_discount_fields_ok_without_profile(self):
        """Non-discount fields can be written without profile validation."""
        user_no_profile = self.env["res.users"].create(
            {
                "name": "No Profile User 2",
                "login": "no_profile_user2_tcp",
                "groups_id": [
                    (4, self.env.ref("sales_team.group_sale_salesman").id),
                    (
                        4,
                        self.env.ref("tr_commercial_policy.group_sales_manager").id,
                    ),
                ],
            }
        )
        # Writing pricelist should not require profile
        self.condition.with_user(user_no_profile).write(
            {"pricelist_id": self.pricelist.id}
        )

    def test_line_extra_discount_requires_manager(self):
        """Extra discount on condition line requires manager group."""
        # Create a salesperson without manager group
        salesperson_basic = self.env["res.users"].create(
            {
                "name": "Basic Salesperson",
                "login": "basic_salesperson_tcp",
                "groups_id": [
                    (4, self.env.ref("sales_team.group_sale_salesman").id),
                ],
            }
        )
        salesperson_basic.partner_id.sales_profile_id = self.internal_profile
        # Salesperson has profile but no manager group
        # ir.model.access prevents writing → use sudo for ORM access
        # but with_user for profile validation
        with self.assertRaises(AccessError):
            self.env["partner.commercial.condition.line"].with_user(
                salesperson_basic
            ).create(
                {
                    "condition_id": self.condition.id,
                    "product_tmpl_id": self.product_template_a.id,
                    "seller_discount": 3.0,
                    "extra_discount": 2.0,
                }
            )

    def test_line_extra_discount_manager_ok(self):
        """Manager can set extra discount on condition line."""
        self.manager_user.partner_id.sales_profile_id = self.internal_profile
        line = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.manager_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "product_tmpl_id": self.product_template_a.id,
                    "seller_discount": 3.0,
                    "extra_discount": 2.0,
                }
            )
        )
        self.assertAlmostEqual(line.extra_discount, 2.0, places=2)

    def test_line_seller_discount_exceeds_rule(self):
        """Seller discount on condition line exceeding rule max is blocked."""
        # internal_general_rule max is 15% (highest band)
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition.line"].with_user(
                self.salesperson
            ).create(
                {
                    "condition_id": self.condition.id,
                    "product_tmpl_id": self.product_template_a.id,
                    "seller_discount": 20.0,
                }
            )


@tagged("post_install", "-at_install")
class TestGap3AverageOrderAmount(CommercialPolicyTestCommon):
    """Gap 3 — Average order amount for last 6 months."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_internal_policy()
        # Profile now resolves from condition's partner (agent → company default).
        # Set company default to internal profile so validation uses order value bands.
        cls.env.company.default_sales_profile_id = cls.internal_profile
        # Clear salesperson's agent profile so _check_internal_profile_source
        # falls through to company default (which matches internal_profile).
        cls.salesperson.partner_id.sales_profile_id = False
        cls.salesperson.groups_id = [
            (4, cls.env.ref("sales_team.group_sale_salesman").id),
            (
                4,
                cls.env.ref("tr_commercial_policy.group_sales_manager").id,
            ),
        ]

    def test_avg_with_orders(self):
        """Average is correctly computed from confirmed orders."""
        CondModel = self.env["partner.commercial.condition"]
        # Create two confirmed orders in the last 6 months
        order1 = self._create_order()
        self._create_order_line(order1, base_price=2000.0)
        order1.action_confirm()

        order2 = self._create_order()
        self._create_order_line(order2, base_price=4000.0)
        order2.action_confirm()

        avg = CondModel._get_partner_avg_order_amount(self.customer)
        expected = (order1.amount_untaxed + order2.amount_untaxed) / 2
        self.assertAlmostEqual(avg, expected, places=2)

    def test_avg_no_orders(self):
        """Average is 0 when customer has no confirmed orders."""
        CondModel = self.env["partner.commercial.condition"]
        avg = CondModel._get_partner_avg_order_amount(self.customer)
        self.assertAlmostEqual(avg, 0.0, places=2)

    def test_avg_excludes_draft_orders(self):
        """Average excludes draft (unconfirmed) orders."""
        CondModel = self.env["partner.commercial.condition"]
        order = self._create_order()
        self._create_order_line(order, base_price=5000.0)
        # Don't confirm — stays draft
        avg = CondModel._get_partner_avg_order_amount(self.customer)
        self.assertAlmostEqual(avg, 0.0, places=2)

    def test_seller_discount_validation_uses_avg(self):
        """Seller discount on condition uses 6-month avg for internal profile."""
        # Create a confirmed order with amount ~3000 (between 1000 and 5000)
        order = self._create_order()
        self._create_order_line(order, base_price=3000.0)
        order.action_confirm()

        # With avg ~3000, band 1000->5% applies
        # seller_discount of 6% should be blocked
        with self.assertRaises(ValidationError):
            self.condition.with_user(self.salesperson).write({"seller_discount": 6.0})

        # seller_discount of 5% should be OK
        self.condition.with_user(self.salesperson).write({"seller_discount": 5.0})


@tagged("post_install", "-at_install")
class TestGap4BandApprovalLevel(CommercialPolicyTestCommon):
    """Gap 4 — discount_approval_level when order amount < band minimum."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_internal_policy()
        # Profile now resolves from condition (agent -> company default).
        cls.env.company.default_sales_profile_id = cls.internal_profile
        # Clear salesperson's agent profile so _check_internal_profile_source
        # falls through to company default (which matches internal_profile).
        cls.salesperson.partner_id.sales_profile_id = False

    def test_order_below_band_sets_director_level(self):
        """Order below band minimum sets discount_approval_level to director."""
        order = self._create_order()
        # Create two lines: one large to get into band, one with discount
        big_line = self._create_order_line(order, base_price=5500.0)
        line = self._create_order_line(order, product=self.product_b, base_price=100.0)
        line.write({"seller_discount": 8.0})
        # Remove the big line -> amount drops to ~100
        big_line.unlink()
        order.invalidate_recordset(["discount_approval_level"])
        self.assertEqual(order.discount_approval_level, "director")

    def test_order_above_band_allows_confirm(self):
        """Order meeting band minimum confirms without issue."""
        order = self._create_order()
        self._create_order_line(order, base_price=6000.0)
        line = self._create_order_line(order, product=self.product_b, base_price=100.0)
        line.write({"seller_discount": 4.0})
        # amount_untaxed ~6100 >= band min 1000 for 5% -> OK (4% < 5%)
        order.action_confirm()
        self.assertEqual(order.state, "sale")

    def test_band_check_skipped_for_agent(self):
        """Agent profile does not generate band issues."""
        # Switch condition to resolve to agent_profile via company default
        self.env.company.default_sales_profile_id = self.agent_profile
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        line.write({"seller_discount": 5.0})
        # Agent profile -> no band issues
        order.action_confirm()
        self.assertEqual(order.state, "sale")

    def test_no_seller_discount_no_check(self):
        """Lines with no seller discount skip the band check."""
        order = self._create_order()
        self._create_order_line(order, base_price=100.0)
        # No seller_discount -> no band check needed
        order.action_confirm()
        self.assertEqual(order.state, "sale")


@tagged("post_install", "-at_install")
class TestGap5BlockDirectPriceEdit(CommercialPolicyTestCommon):
    """Gap 5 — Block direct editing of price_unit and discount."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_direct_price_unit_write_blocked(self):
        """Direct write to price_unit is blocked when profile is active."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        with self.assertRaises(ValidationError):
            line.with_user(self.salesperson).write({"price_unit": 50.0})

    def test_direct_discount_write_blocked(self):
        """Direct write to discount is blocked when profile is active."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        with self.assertRaises(ValidationError):
            line.with_user(self.salesperson).write({"discount": 10.0})

    def test_seller_discount_write_allowed(self):
        """Write to seller_discount is allowed (not price_unit/discount)."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        line.with_user(self.salesperson).write({"seller_discount": 5.0})
        self.assertAlmostEqual(line.seller_discount, 5.0, places=2)

    def test_admin_bypasses_protection(self):
        """Admin can still write price_unit directly."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        # Superuser should bypass
        line.write({"price_unit": 50.0})
        self.assertAlmostEqual(line.price_unit, 50.0, places=2)

    def test_no_profile_blocks_direct_edit(self):
        """Direct editing is blocked even when no profile is active (policy 4.1)."""
        self.salesperson.partner_id.sales_profile_id = False
        # Clear company default so profile can't resolve via fallback
        self.env.company.default_sales_profile_id = False
        order = self._create_order()
        self.assertFalse(order.sales_profile_id)
        line = self._create_order_line(order)
        with self.assertRaises(ValidationError):
            line.with_user(self.salesperson).write({"price_unit": 50.0})

    def test_compute_recomputes_via_context(self):
        """Internal recompute via policy still works (context bypass)."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        # Changing seller_discount triggers _recompute_price_unit_from_policy
        # which should successfully write price_unit via context bypass
        line.seller_discount = 10.0
        expected = 100.0 * (1 - 10.0 / 100)
        self.assertAlmostEqual(line.price_unit, expected, places=2)

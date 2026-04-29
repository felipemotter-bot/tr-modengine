# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).


from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestConfirmedOrderFrozen(CommercialPolicyTestCommon):
    """Confirmed orders are not altered by compute recompute."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_confirmed_order_frozen_on_condition_change(self):
        """Changing customer condition does not alter confirmed order."""
        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "pricelist_id": self.pricelist.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "product_uom_qty": 10,
                        },
                    )
                ],
            }
        )
        order.action_confirm()
        price_before = order.order_line[0].price_unit
        discount_before = order.order_line[0].discount

        # Change condition discounts via SQL to bypass validation
        self.env.cr.execute(
            """
            UPDATE partner_commercial_condition
            SET cash_discount = 10, fob_discount = 10, seller_discount = 15
            WHERE id = %s
            """,
            (self.condition.id,),
        )
        self.condition.invalidate_recordset()

        # Recompute should not touch confirmed order
        order.order_line._compute_base_price()
        order.order_line._compute_reference_price()
        order.order_line._compute_price_unit()

        self.assertEqual(order.order_line[0].price_unit, price_before)
        self.assertEqual(order.order_line[0].discount, discount_before)

    def test_confirmed_order_profile_frozen(self):
        """Sales profile does not change on confirmed order."""
        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "pricelist_id": self.pricelist.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "product_uom_qty": 10,
                        },
                    )
                ],
            }
        )
        order.action_confirm()
        profile_before = order.sales_profile_id

        # Recompute should keep the same profile
        order._compute_sales_profile_id()
        self.assertEqual(order.sales_profile_id, profile_before)

    def test_confirmed_order_condition_frozen(self):
        """Changing partner condition does not alter confirmed order."""
        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "pricelist_id": self.pricelist.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "product_uom_qty": 10,
                        },
                    )
                ],
            }
        )
        order.action_confirm()
        condition_before = order.commercial_condition_id

        # Change the existing condition's pricelist via SQL (bypass ORM)
        # to simulate a changed partner setup
        other_pricelist = self.env["product.pricelist"].create(
            {"name": "Other PL", "currency_id": self.env.ref("base.BRL").id}
        )
        self.env.cr.execute(
            """
            UPDATE partner_commercial_condition
            SET pricelist_id = %s
            WHERE id = %s
            """,
            (other_pricelist.id, self.condition.id),
        )
        self.condition.invalidate_recordset()

        # Recompute should not touch confirmed order
        order._compute_commercial_condition_id()
        self.assertEqual(order.commercial_condition_id, condition_before)

    def test_draft_order_absorbs_policy(self):
        """Draft orders (even pre-existing) follow the policy."""
        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "pricelist_id": self.pricelist.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "product_uom_qty": 10,
                        },
                    )
                ],
            }
        )
        self.assertEqual(order.state, "draft")
        # Policy fields should be populated
        self.assertTrue(order.commercial_condition_id)
        self.assertTrue(order.sales_profile_id)
        self.assertGreater(order.order_line[0].base_price, 0)


@tagged("post_install", "-at_install")
class TestReloadWithContextFlag(CommercialPolicyTestCommon):
    """Reload works on confirmed orders via force_policy_recompute."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_reload_updates_confirmed_order(self):
        """_apply_reload_conditions works on confirmed orders."""
        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "pricelist_id": self.pricelist.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "product_uom_qty": 10,
                        },
                    )
                ],
            }
        )
        order.action_confirm()
        price_before = order.order_line[0].price_unit

        # Change condition to different discounts
        self.condition.write({"seller_discount": 8.0})

        # Reload should update confirmed order
        order._apply_reload_conditions()

        # Price should have changed (different seller_discount)
        self.assertNotEqual(order.order_line[0].price_unit, price_before)

    def test_compute_skips_confirmed_without_force(self):
        """Computes skip confirmed orders without force flag."""
        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "pricelist_id": self.pricelist.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "product_uom_qty": 10,
                        },
                    )
                ],
            }
        )
        order.action_confirm()
        base_price_before = order.order_line[0].base_price

        # Direct compute call without context flag — should be no-op
        order.order_line._compute_base_price()
        self.assertEqual(order.order_line[0].base_price, base_price_before)

    def test_compute_runs_confirmed_with_force(self):
        """Computes run on confirmed orders with force flag."""
        order = self.env["sale.order"].create(
            {
                "partner_id": self.customer.id,
                "pricelist_id": self.pricelist.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "product_uom_qty": 10,
                        },
                    )
                ],
            }
        )
        order.action_confirm()

        # Zero out base_price via SQL to prove compute actually runs
        self.env.cr.execute(
            "UPDATE sale_order_line SET base_price = 0 WHERE id = %s",
            (order.order_line[0].id,),
        )
        order.order_line.invalidate_recordset(["base_price"])

        order.order_line.with_context(force_policy_recompute=True)._compute_base_price()

        self.assertGreater(order.order_line[0].base_price, 0)

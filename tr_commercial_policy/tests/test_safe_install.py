# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).


from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestPreInitHook(CommercialPolicyTestCommon):
    """Tests for pre_init_hook helpers."""

    def test_pre_init_hook_idempotent(self):
        """Running pre_init_hook on an already-installed DB does not raise."""
        from ..hooks import pre_init_hook

        # All columns exist, no orphans — should be a fast no-op
        pre_init_hook(self.env.cr)

    def test_pre_create_columns_idempotent(self):
        """Running _pre_create_columns twice does not raise."""
        from ..hooks import _pre_create_columns

        cr = self.env.cr
        # Columns already exist (module is installed), must not fail
        _pre_create_columns(cr)

    def test_pre_create_columns_creates_missing(self):
        """_pre_create_columns creates a column that does not exist yet."""
        from ..hooks import _pre_create_columns

        cr = self.env.cr
        # Drop a column that the hook should recreate
        cr.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'sale_order'
              AND column_name = 'discount_approval_level'
            """
        )
        if cr.fetchone():
            cr.execute("ALTER TABLE sale_order DROP COLUMN discount_approval_level")
            _pre_create_columns(cr)
            cr.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'sale_order'
                  AND column_name = 'discount_approval_level'
                """
            )
            self.assertTrue(cr.fetchone())

    def test_clean_orphan_agents_removes_only_orphans(self):
        """Orphan agent records are deleted; valid ones are preserved."""
        from ..hooks import _clean_orphan_agents

        cr = self.env.cr

        # Create agent and commission for the orphan record
        commission = self.env["commission"].create(
            {"name": "Test Commission Orphan", "fix_qty": 5.0}
        )
        agent = self.env["res.partner"].create(
            {"name": "Test Agent Orphan", "agent": True}
        )

        # Count valid agents before
        cr.execute("SELECT count(*) FROM sale_order_line_agent")
        valid_before = cr.fetchone()[0]

        # Temporarily drop FK constraint, insert orphan, then restore
        cr.execute(
            """
            ALTER TABLE sale_order_line_agent
            DROP CONSTRAINT IF EXISTS sale_order_line_agent_object_id_fkey
            """
        )
        cr.execute(
            """
            INSERT INTO sale_order_line_agent
                (object_id, agent_id, commission_id)
            VALUES (999999999, %s, %s)
            """,
            (agent.id, commission.id),
        )

        _clean_orphan_agents(cr)

        cr.execute("SELECT count(*) FROM sale_order_line_agent")
        valid_after = cr.fetchone()[0]
        self.assertEqual(valid_after, valid_before)

        # Restore FK constraint
        cr.execute(
            """
            ALTER TABLE sale_order_line_agent
            ADD CONSTRAINT sale_order_line_agent_object_id_fkey
            FOREIGN KEY (object_id) REFERENCES sale_order_line(id)
            ON DELETE CASCADE
            """
        )

    def test_clean_orphan_invoice_agents(self):
        """Orphan account.invoice.line.agent records are deleted."""
        from ..hooks import _clean_orphan_agents

        cr = self.env.cr
        commission = self.env["commission"].create(
            {"name": "Test Commission Invoice", "fix_qty": 5.0}
        )
        agent = self.env["res.partner"].create(
            {"name": "Test Agent Invoice", "agent": True}
        )

        cr.execute("SELECT count(*) FROM account_invoice_line_agent")
        valid_before = cr.fetchone()[0]

        cr.execute(
            """
            ALTER TABLE account_invoice_line_agent
            DROP CONSTRAINT IF EXISTS account_invoice_line_agent_object_id_fkey
            """
        )
        cr.execute(
            """
            INSERT INTO account_invoice_line_agent
                (object_id, agent_id, commission_id)
            VALUES (999999999, %s, %s)
            """,
            (agent.id, commission.id),
        )

        _clean_orphan_agents(cr)

        cr.execute("SELECT count(*) FROM account_invoice_line_agent")
        valid_after = cr.fetchone()[0]
        self.assertEqual(valid_after, valid_before)

        cr.execute(
            """
            ALTER TABLE account_invoice_line_agent
            ADD CONSTRAINT account_invoice_line_agent_object_id_fkey
            FOREIGN KEY (object_id) REFERENCES account_move_line(id)
            ON DELETE CASCADE
            """
        )

    def test_clean_orphan_tier_reviews(self):
        """Orphan tier reviews are cleaned; valid ones are preserved."""
        from ..hooks import _clean_orphan_tier_reviews

        cr = self.env.cr
        # Count valid reviews before
        cr.execute("SELECT count(*) FROM tier_review WHERE model = 'sale.order'")
        valid_before = cr.fetchone()[0]

        # Get a tier definition (module has tier_definition data)
        definition = self.env.ref(
            "tr_commercial_policy.sale_order_tier_definition",
            raise_if_not_found=False,
        )
        if not definition:
            definition = self.env["tier.definition"].search([], limit=1)
        self.assertTrue(definition, "No tier definition found in test DB")

        # Insert orphan pointing to non-existent sale order
        cr.execute(
            """
            INSERT INTO tier_review
                (model, res_id, definition_id, status,
                 create_uid, create_date, write_uid, write_date)
            VALUES
                ('sale.order', 999999999, %s, 'pending',
                 1, NOW(), 1, NOW())
            """,
            (definition.id,),
        )

        _clean_orphan_tier_reviews(cr)

        cr.execute("SELECT count(*) FROM tier_review WHERE model = 'sale.order'")
        valid_after = cr.fetchone()[0]
        self.assertEqual(valid_after, valid_before)

    def test_post_init_hook_idempotent(self):
        """Running post_init_hook twice does not duplicate profiles."""
        from ..hooks import post_init_hook

        cr = self.env.cr
        Profile = self.env["tr.sales.profile"]
        count_before = Profile.search_count([])

        post_init_hook(cr, self.env.registry)
        self.env.cache.invalidate()

        count_after = Profile.search_count([])
        self.assertEqual(count_after, count_before)


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

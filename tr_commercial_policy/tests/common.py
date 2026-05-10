# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests.common import TransactionCase


class CommercialPolicyTestCommon(TransactionCase):
    # Mirror hooks._PLACEHOLDER_SEQUENCE so any rule the user adds later
    # (default sequence 10) wins resolution against this placeholder.
    _PLACEHOLDER_SEQUENCE = 999

    @classmethod
    def _placeholder_rule_vals(cls, profile_type):
        """One2many vals for a placeholder rule.

        Required since tr.sales.profile demands at least one rule.
        Uses a 100% band so the placeholder accepts any realistic
        discount; admins should override with real bands.
        """
        if profile_type == "agent":
            band_field = "commission_band_ids"
            band_vals = {"discount_up_to": 100.0, "commission_rate": 0.0}
        else:
            band_field = "order_value_band_ids"
            band_vals = {"order_min_amount": 0.0, "seller_discount_max": 100.0}
        return [
            (
                0,
                0,
                {
                    "applied_on": "general",
                    "sequence": cls._PLACEHOLDER_SEQUENCE,
                    band_field: [(0, 0, band_vals)],
                },
            ),
        ]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.company = cls.env.ref("base.main_company")

        # Configure policy rates with meaningful test values so compute paths
        # (calc_adjustment_factor, reference_price, etc.) are exercised. Each
        # test gets 10% tax + 5% freight + 2% admin unless it overrides.
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("tr_commercial_policy.tax_rate_pct", "10.0")
        icp.set_param("tr_commercial_policy.freight_rate_pct", "5.0")
        icp.set_param("tr_commercial_policy.admin_rate_pct", "2.0")

        # Products
        cls.categ_chemicals = cls.env["product.category"].create({"name": "Chemicals"})
        cls.categ_solvents = cls.env["product.category"].create(
            {
                "name": "Solvents",
                "parent_id": cls.categ_chemicals.id,
            }
        )

        cls.product_template_a = cls.env["product.template"].create(
            {
                "name": "Product A",
                "type": "consu",
                "list_price": 100.0,
                "categ_id": cls.categ_chemicals.id,
            }
        )
        cls.product_a = cls.product_template_a.product_variant_ids[0]

        cls.product_template_b = cls.env["product.template"].create(
            {
                "name": "Product B",
                "type": "consu",
                "list_price": 200.0,
                "categ_id": cls.categ_solvents.id,
            }
        )
        cls.product_b = cls.product_template_b.product_variant_ids[0]

        # Partners
        # ind_final="0" so sale.order Form().save() doesn't fail validation
        # when fiscal_operation_id defaults from the company.
        cls.customer = cls.env["res.partner"].create(
            {"name": "Test Customer", "ind_final": "0"}
        )
        cls.customer_group = cls.env["res.partner"].create(
            {"name": "Test Group (Parent)", "ind_final": "0"}
        )

        # Sales team
        cls.team = cls.env["crm.team"].create({"name": "Test Team"})

        # Salesperson user
        cls.salesperson = cls.env["res.users"].create(
            {
                "name": "Test Salesperson",
                "login": "test_salesperson_tcp",
                "groups_id": [
                    (4, cls.env.ref("sales_team.group_sale_salesman").id),
                ],
            }
        )

        # Manager user (can approve up to manager_extra_limit)
        cls.manager_user = cls.env["res.users"].create(
            {
                "name": "Test Manager",
                "login": "test_manager_tcp",
                "groups_id": [
                    (4, cls.env.ref("sales_team.group_sale_salesman").id),
                    (
                        4,
                        cls.env.ref("tr_commercial_policy.group_sales_manager").id,
                    ),
                ],
            }
        )

        # Director user (can approve any extra discount)
        cls.director_user = cls.env["res.users"].create(
            {
                "name": "Test Director",
                "login": "test_director_tcp",
                "groups_id": [
                    (4, cls.env.ref("sales_team.group_sale_salesman").id),
                    (
                        4,
                        cls.env.ref("tr_commercial_policy.group_sales_director").id,
                    ),
                ],
            }
        )

        # Pricelist (created before profiles so it can be assigned)
        cls.pricelist = cls.env["product.pricelist"].create(
            {
                "name": "Test Pricelist",
                "currency_id": cls.env.ref("base.BRL").id,
            }
        )

        # Agent profile (with placeholder rule to satisfy constraint)
        cls.agent_profile = cls.env["tr.sales.profile"].create(
            {
                "name": "Agent Profile",
                "profile_type": "agent",
                "pricelist_ids": [(6, 0, [cls.pricelist.id])],
                "cash_discount_max": 5.0,
                "fob_discount_max": 3.0,
                "cash_term_avg_days_max": 30,
                "manager_extra_limit": 5.0,
                "rule_ids": cls._placeholder_rule_vals("agent"),
            }
        )

        # Internal profile (with placeholder rule to satisfy constraint)
        cls.internal_profile = cls.env["tr.sales.profile"].create(
            {
                "name": "Internal Profile",
                "profile_type": "internal",
                "pricelist_ids": [(6, 0, [cls.pricelist.id])],
                "cash_discount_max": 8.0,
                "fob_discount_max": 5.0,
                "cash_term_avg_days_max": 45,
                "manager_extra_limit": 3.0,
                "rule_ids": cls._placeholder_rule_vals("internal"),
            }
        )

        # Set the company-default profile so creating a commercial
        # condition without a configured agent/salesperson/team still
        # resolves through the fallback branch and passes the
        # ``_check_applicable_profile_resolved`` constraint introduced
        # in 16.0.2.4.0. Tests that need to exercise the "no profile
        # resolvable" path can clear this default explicitly.
        cls.company.default_sales_profile_id = cls.agent_profile

        # Payment terms
        cls.payment_term_short = cls.env["account.payment.term"].create(
            {
                "name": "Short Term (15 days)",
                "line_ids": [
                    (0, 0, {"value": "balance", "days": 15}),
                ],
            }
        )
        cls.payment_term_long = cls.env["account.payment.term"].create(
            {
                "name": "Long Term (60 days)",
                "line_ids": [
                    (0, 0, {"value": "balance", "days": 60}),
                ],
            }
        )

    @classmethod
    def _setup_commercial_policy(cls):
        """Set up commercial condition and profile for Phase 2 tests.

        Call this in setUpClass of test classes that need the full
        discount engine setup.
        """
        # Reuse the placeholder general rule from setUpClass and replace
        # its bands with the real ones for this test setup.
        cls.general_rule = cls.agent_profile.rule_ids.filtered(
            lambda r: r.applied_on == "general"
        )[:1]
        cls.general_rule.commission_band_ids.unlink()
        cls.general_rule.write(
            {
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 5.0, "commission_rate": 10.0}),
                    (0, 0, {"discount_up_to": 10.0, "commission_rate": 7.0}),
                ],
            }
        )

        # Assign profile to salesperson and as company default (before
        # creating condition so the profile-based validation works)
        cls.salesperson.partner_id.sales_profile_id = cls.agent_profile
        cls.env.company.default_sales_profile_id = cls.agent_profile

        # Commercial condition for customer (created with salesperson who
        # has a profile — validates discounts against profile limits)
        cls.condition = (
            cls.env["partner.commercial.condition"]
            .with_user(cls.salesperson)
            .create(
                {
                    "partner_id": cls.customer.id,
                    "pricelist_id": cls.pricelist.id,
                    "cash_discount": 2.0,
                    "fob_discount": 1.0,
                    "seller_discount": 5.0,
                }
            )
        )
        cls.customer.commercial_condition_id = cls.condition

    @classmethod
    def _setup_internal_policy(cls):
        """Set up internal profile with order value bands.

        Call after _setup_commercial_policy when testing internal profiles.
        """
        # Reuse the placeholder general rule from setUpClass and replace
        # its bands with the real ones for this test setup.
        cls.internal_general_rule = cls.internal_profile.rule_ids.filtered(
            lambda r: r.applied_on == "general"
        )[:1]
        cls.internal_general_rule.order_value_band_ids.unlink()
        cls.internal_general_rule.write(
            {
                "order_value_band_ids": [
                    (
                        0,
                        0,
                        {
                            "order_min_amount": 1000.0,
                            "seller_discount_max": 5.0,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "order_min_amount": 5000.0,
                            "seller_discount_max": 10.0,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "order_min_amount": 10000.0,
                            "seller_discount_max": 15.0,
                        },
                    ),
                ],
            }
        )

    @classmethod
    def _setup_commission_bands(cls):
        """Alias — bands are now created in _setup_commercial_policy.

        Sets convenience references to the bands already on general_rule.
        """
        bands = cls.general_rule.commission_band_ids.sorted("discount_up_to")
        cls.band_low = bands[0]
        cls.band_high = bands[1]

    @classmethod
    def _setup_agent(cls):
        """Set up an agent partner with commission for commission tests.

        Call this after _setup_commercial_policy and _setup_commission_bands.
        """
        # Commission object (fixed type)
        cls.commission = cls.env["commission"].create(
            {
                "name": "Test Commission",
                "commission_type": "fixed",
                "fix_qty": 10.0,
            }
        )
        # Agent partner (with sales profile for profile resolution)
        cls.agent_partner = cls.env["res.partner"].create(
            {
                "name": "Test Agent",
                "agent": True,
                "commission_id": cls.commission.id,
                "sales_profile_id": cls.agent_profile.id,
            }
        )
        # Assign agent to customer
        cls.customer.agent_ids = [(4, cls.agent_partner.id)]

    @classmethod
    def _create_order(cls, **kwargs):
        """Helper to create a sale order with defaults."""
        vals = {
            "partner_id": cls.customer.id,
            "user_id": cls.salesperson.id,
        }
        vals.update(kwargs)
        return cls.env["sale.order"].create(vals)

    @classmethod
    def _create_order_line(cls, order, product=None, qty=1, **kwargs):
        """Helper to create a sale order line."""
        vals = {
            "order_id": order.id,
            "product_id": (product or cls.product_a).id,
            "product_uom_qty": qty,
        }
        vals.update(kwargs)
        return cls.env["sale.order.line"].create(vals)

    # --- Invoice helpers ---

    @classmethod
    def _create_confirmed_order_with_invoice(cls, seller_discount=5.0, qty=10, **kw):
        """Create sale order, confirm, generate invoice. Returns (order, invoice).

        Sets fiscal_operation_id=False and invoice_policy='order' for
        standard invoicing path (no l10n_br complications).
        """
        cls.product_template_a.invoice_policy = "order"
        order = cls._create_order(**kw)
        order.fiscal_operation_id = False
        line = cls._create_order_line(order, qty=qty)
        line.seller_discount = seller_discount
        line.extra_discount = 0.0
        order.action_confirm()
        invoice = order._create_invoices()
        return order, invoice

    @classmethod
    def _create_manual_invoice(cls, partner=None, **kw):
        """Create an out_invoice manually (no sale order origin)."""
        vals = {
            "move_type": "out_invoice",
            "partner_id": (partner or cls.customer).id,
        }
        vals.update(kw)
        return cls.env["account.move"].create(vals)

    def _assert_invoice_matches_sale_snapshot(self, invoice):
        """Assert all policy fields on invoice match the sale order."""
        for inv_line in invoice.invoice_line_ids.filtered(
            lambda line: line.sale_line_ids and line.display_type == "product"
        ):
            sale_line = inv_line.sale_line_ids[0]
            self.assertAlmostEqual(
                inv_line.seller_discount,
                sale_line.seller_discount,
                places=2,
                msg="seller_discount mismatch",
            )
            self.assertAlmostEqual(
                inv_line.extra_discount,
                sale_line.extra_discount,
                places=2,
                msg="extra_discount mismatch",
            )
            self.assertAlmostEqual(
                inv_line.base_price,
                sale_line.base_price,
                places=2,
                msg="base_price mismatch",
            )
            self.assertAlmostEqual(
                inv_line.reference_price,
                sale_line.reference_price,
                places=2,
                msg="reference_price mismatch",
            )
            self.assertAlmostEqual(
                inv_line.commission_rate,
                sale_line.commission_rate,
                places=2,
                msg="commission_rate mismatch",
            )
            self.assertEqual(
                inv_line.extra_discount_reason or False,
                sale_line.extra_discount_reason or False,
                "extra_discount_reason mismatch",
            )
            # Agent snapshot
            sale_agents = {
                (agent.agent_id.id, agent.commission_id.id)
                for agent in sale_line.agent_ids
            }
            inv_agents = {
                (agent.agent_id.id, agent.commission_id.id)
                for agent in inv_line.agent_ids
            }
            self.assertEqual(inv_agents, sale_agents, "agent snapshot mismatch")
        # Header fields
        orders = invoice.invoice_line_ids.sale_line_ids.mapped("order_id")
        if orders:
            order = orders[0]
            self.assertAlmostEqual(
                invoice.tr_cash_discount,
                order.cash_discount,
                places=2,
                msg="tr_cash_discount mismatch",
            )
            self.assertAlmostEqual(
                invoice.tr_fob_discount,
                order.fob_discount,
                places=2,
                msg="tr_fob_discount mismatch",
            )
            self.assertAlmostEqual(
                invoice.tr_contractual_return,
                order.contractual_return,
                places=2,
                msg="tr_contractual_return mismatch",
            )
            self.assertEqual(
                invoice.invoice_payment_term_id,
                order.payment_term_id,
                "invoice_payment_term_id mismatch",
            )

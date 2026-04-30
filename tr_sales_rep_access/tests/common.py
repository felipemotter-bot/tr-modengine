# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests.common import TransactionCase


class SalesRepAccessTestCommon(TransactionCase):
    """Common setup for tr_sales_rep_access tests.

    Two agents (A1, A2) each linked to a rep user (U1, U2) and three
    customers:

    - C1 with ``agent_ids = [A1]`` → visible to U1, owner of orders by U1.
    - C2 with ``agent_ids = [A2]`` → visible to U2 only.
    - C3 with no agent → visible to neither rep (snapshot NULL on orders).
    """

    @classmethod
    def _make_sales_profile(cls, name):
        """Create a minimal agent profile satisfying tr_commercial_policy."""
        return cls.env["tr.sales.profile"].create(
            {
                "name": name,
                "profile_type": "agent",
                "pricelist_ids": [(6, 0, [cls.pricelist.id])],
                "cash_discount_max": 0.0,
                "fob_discount_max": 0.0,
                "cash_term_avg_days_max": 30,
                "manager_extra_limit": 0.0,
                "rule_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "general",
                            "sequence": 999,
                            "commission_band_ids": [
                                (
                                    0,
                                    0,
                                    {
                                        "discount_up_to": 100.0,
                                        "commission_rate": 0.0,
                                    },
                                )
                            ],
                        },
                    ),
                ],
            }
        )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.company = cls.env.ref("base.main_company")
        cls.rep_group = cls.env.ref("tr_sales_rep_access.group_sales_rep_external")

        # Pricelist shared by profiles.
        cls.pricelist = cls.env["product.pricelist"].create(
            {
                "name": "Sales Rep Access Test Pricelist",
                "currency_id": cls.env.ref("base.BRL").id,
            }
        )

        # Category tree used by PR 2 (catalog) tests. Root → allowed
        # (has a sub-branch) and → other. A1's catalog allows `allowed`
        # and excludes `excluded_sub` (descendant of allowed).
        cls.cat_root = cls.env["product.category"].create(
            {"name": "Sales Rep Access Root"}
        )
        cls.cat_allowed = cls.env["product.category"].create(
            {"name": "Allowed", "parent_id": cls.cat_root.id}
        )
        cls.cat_allowed_sub = cls.env["product.category"].create(
            {"name": "Allowed Sub", "parent_id": cls.cat_allowed.id}
        )
        cls.cat_excluded_sub = cls.env["product.category"].create(
            {"name": "Excluded Sub", "parent_id": cls.cat_allowed.id}
        )
        cls.cat_other = cls.env["product.category"].create(
            {"name": "Other", "parent_id": cls.cat_root.id}
        )

        # Product used in order lines. `invoice_policy='order'` is the
        # core default for consumable products, set explicitly as defense
        # in depth so `_create_invoices` produces an invoice regardless
        # of overrides from trento_* modules.
        cls.product = cls.env["product.product"].create(
            {
                "name": "Sales Rep Access Test Product",
                "type": "consu",
                "list_price": 100.0,
                "invoice_policy": "order",
                "categ_id": cls.cat_allowed.id,
            }
        )
        # Additional products placed in different branches to exercise
        # the catalog filter in PR 2 tests.
        cls.product_allowed_sub = cls.env["product.product"].create(
            {
                "name": "Product Allowed Sub",
                "type": "consu",
                "list_price": 50.0,
                "invoice_policy": "order",
                "categ_id": cls.cat_allowed_sub.id,
            }
        )
        cls.product_excluded = cls.env["product.product"].create(
            {
                "name": "Product Excluded",
                "type": "consu",
                "list_price": 50.0,
                "invoice_policy": "order",
                "categ_id": cls.cat_excluded_sub.id,
            }
        )
        cls.product_other = cls.env["product.product"].create(
            {
                "name": "Product Other",
                "type": "consu",
                "list_price": 50.0,
                "invoice_policy": "order",
                "categ_id": cls.cat_other.id,
            }
        )

        # Base commission used by both agents. tr_commercial_policy's
        # _resolve_agent_commissions reads agent.commission_id to resolve
        # the managed commission for each line; without it, the agent
        # line gets commission_id NULL and violates the NOT NULL
        # constraint.
        cls.commission = cls.env["commission"].create(
            {
                "name": "Sales Rep Access Test Commission",
                "commission_type": "fixed",
                "fix_qty": 0.0,
            }
        )

        # Sales profiles (one per agent; tr_commercial_policy demands a
        # profile before agent_ids is accepted on a partner).
        cls.profile_a1 = cls._make_sales_profile("A1 Profile")
        cls.profile_a2 = cls._make_sales_profile("A2 Profile")

        # Set the company-default profile so commercial conditions
        # created by these tests have a fallback when the partner has
        # no agent. After the multi-company resolution fix in
        # ``tr_commercial_policy``, conditions without a resolvable
        # profile are blocked by ``_check_applicable_profile_resolved``.
        cls.company.default_sales_profile_id = cls.profile_a1

        # Agents A1 and A2 are res.partner records with agent=True,
        # sales_profile_id and commission_id.
        cls.agent_a1 = cls.env["res.partner"].create(
            {
                "name": "Agent A1",
                "agent": True,
                "sales_profile_id": cls.profile_a1.id,
                "commission_id": cls.commission.id,
            }
        )
        cls.agent_a2 = cls.env["res.partner"].create(
            {
                "name": "Agent A2",
                "agent": True,
                "sales_profile_id": cls.profile_a2.id,
                "commission_id": cls.commission.id,
            }
        )

        # Rep users U1, U2 linked to agent partners and added to the rep
        # group. Users inherit from base.group_user via res.users default;
        # the rep group is set explicitly and exclusively.
        cls.user_u1 = cls.env["res.users"].create(
            {
                "name": "Rep U1",
                "login": "tsra_u1",
                "partner_id": cls.agent_a1.id,
                "groups_id": [(6, 0, [cls.rep_group.id])],
            }
        )
        cls.user_u2 = cls.env["res.users"].create(
            {
                "name": "Rep U2",
                "login": "tsra_u2",
                "partner_id": cls.agent_a2.id,
                "groups_id": [(6, 0, [cls.rep_group.id])],
            }
        )

        # PR 2 catalog configuration on A1. A2 intentionally left
        # empty — fail-safe tests rely on A2 having no allowed
        # categories (sees nothing).
        cls.agent_a1.write(
            {
                "allowed_category_ids": [(6, 0, [cls.cat_allowed.id])],
                "excluded_category_ids": [(6, 0, [cls.cat_excluded_sub.id])],
            }
        )

        # Customers.
        cls.customer_c1 = cls.env["res.partner"].create(
            {
                "name": "Customer C1",
                "agent_ids": [(6, 0, [cls.agent_a1.id])],
            }
        )
        cls.customer_c2 = cls.env["res.partner"].create(
            {
                "name": "Customer C2",
                "agent_ids": [(6, 0, [cls.agent_a2.id])],
            }
        )
        cls.customer_c3 = cls.env["res.partner"].create({"name": "Customer C3"})

        # Commercial conditions for the customers, so sale.order tests
        # that call ``action_confirm`` get a resolved profile via the
        # condition's chain (agent → company default). Without these,
        # ``_check_sales_profile_required`` fails because the orders
        # have no ``commercial_condition_id`` and therefore no profile.
        Condition = cls.env["partner.commercial.condition"]
        for customer in (cls.customer_c1, cls.customer_c2, cls.customer_c3):
            cond = Condition.create(
                {
                    "partner_id": customer.id,
                    "pricelist_id": cls.pricelist.id,
                }
            )
            customer.commercial_condition_id = cond

    def _make_invoice(self, customer, rep_agent=None):
        """Create a minimal ``account.move`` for visibility-rule tests.

        Does not go through ``sale.order._create_invoices`` because the
        Brazilian localization requires fiscal operation setup that is
        out of scope for this foundation PR. Writes
        ``sales_rep_partner_id`` explicitly to simulate what the full
        ``_prepare_invoice`` pipeline would have done.
        """
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": customer.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product.id,
                            "quantity": 1.0,
                            "price_unit": 100.0,
                        },
                    ),
                ],
            }
        )
        if rep_agent:
            invoice.sales_rep_partner_id = rep_agent.id
        return invoice

    def _make_order(self, customer):
        """Create a draft sale order for ``customer`` as admin.

        Order creation is done as admin so that ``action_confirm`` and
        ``_create_invoices`` can be invoked without hitting the rep rule
        (rep users cannot confirm in the business flow — the conferente
        tier does it). Tests that validate rep behaviour wrap the
        resulting record with ``.with_user(self.user_u1)``.
        """
        return self.env["sale.order"].create(
            {
                "partner_id": customer.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product.id,
                            "product_uom_qty": 1.0,
                            "price_unit": 100.0,
                        },
                    ),
                ],
            }
        )

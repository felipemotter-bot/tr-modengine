# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest import mock

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.tr_sales_rep_access.reports import sale_report

from .common import SalesRepAccessTestCommon

CONFERENCE_TIER_XMLID = "tr_sales_rep_access.tier_def_sales_rep_order_conference"


@tagged("post_install", "-at_install")
class TestTierConference(SalesRepAccessTestCommon):
    """Coverage for the PR 6b conference tier + print block override.

    Every ``sale.order`` placed by a rep (``sales_rep_partner_id``
    populated) triggers an operational review by the Sales Rep Order
    Checker group. The review runs in parallel with the discount
    approval tiers of ``tr_commercial_policy`` and, unlike those,
    does not block print of the quotation — only pricing tiers do.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Mail ICPs required by ``notify_on_create`` on the tier
        # definition — without them, the first create that fires
        # request_validation would blow up inside ``mail.mail._send``.
        cls.env["ir.config_parameter"].sudo().set_param(
            "mail.catchall.domain", "example.com"
        )
        cls.env["ir.config_parameter"].sudo().set_param(
            "mail.default.from", "noreply@example.com"
        )

        cls.conference_tier = cls.env.ref(CONFERENCE_TIER_XMLID)
        cls.manager_tier = cls.env.ref("tr_commercial_policy.tier_def_discount_manager")
        cls.checker_group = cls.env.ref("tr_sales_rep_access.group_sales_rep_checker")

        # Checker user — has the rep-checker group plus a sale.order
        # access group so they can read the orders they have to
        # approve. ``sales_team.group_sale_salesman`` is enough for
        # read; write on the tier review is granted by the review
        # itself (base_tier_validation's rule on tier.review).
        cls.checker_user = cls.env["res.users"].create(
            {
                "name": "Conference Checker",
                "login": "tsra_checker",
                "email": "checker@example.com",
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            cls.checker_group.id,
                            cls.env.ref("sales_team.group_sale_salesman").id,
                        ],
                    )
                ],
            }
        )

        # Make sure the company print block is ON for the print
        # tests; the underlying flag is off by default in the test
        # db. Flipping it in setUp keeps the guard clauses exercised.
        cls.env.company.sale_report_print_block = True

    # ------------------------------------------------------------------
    # Tier lifecycle
    # ------------------------------------------------------------------

    def _make_rep_order(self, customer):
        # Mirrors ``_make_order`` but creates the sale.order as the
        # rep user — the PR 6b create() override only fires
        # request_validation() when the caller is in the rep group.
        # Admin-created orders (the normal ``_make_order`` helper)
        # skip the tier on purpose; see models/sale_order.py comment.
        return (
            self.env["sale.order"]
            .with_user(self.user_u1)
            .create(
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
        )

    def _make_admin_order_with_injected_reviews(self, customer, definitions):
        # Build an admin-owned sale.order and inject pending
        # ``tier.review`` records directly. Lets each print-block
        # test declare the exact pending-tiers scenario it needs
        # (conference-only, conference+manager, manager-only) without
        # going through ``request_validation()`` — which would lock
        # the order for ``base_tier_validation`` before we can
        # finish setup, and fight with the discount_rate recompute
        # chain from tr_commercial_policy.
        order = (
            self.env["sale.order"]
            .sudo()
            .create(
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
        )
        # Flush all pending computes (discount_rate, etc.) while the
        # order is still tier-free so the inject step doesn't trip
        # on a recompute later.
        self.env.flush_all()
        admin = self.env.ref("base.user_admin")
        for definition in definitions:
            self.env["tier.review"].sudo().create(
                {
                    "model": "sale.order",
                    "res_id": order.id,
                    "definition_id": definition.id,
                    "requested_by": admin.id,
                    "status": "pending",
                }
            )
        # Turn on the company's print block so the override actually
        # has something to short-circuit.
        self.env.company.sale_report_print_block = True
        self.env.flush_all()
        return order

    def test_rep_order_creates_conference_review(self):
        order = self._make_rep_order(self.customer_c1)
        reviews = order.review_ids.filtered(
            lambda r: r.definition_id == self.conference_tier
        )
        self.assertTrue(
            reviews,
            "Expected a Conference tier review immediately after create "
            "when a rep user creates the order.",
        )
        self.assertNotEqual(
            order.validation_status,
            "validated",
            "Order with pending review must not be marked validated.",
        )

    def test_admin_order_does_not_trigger_conference_tier(self):
        # Admin creating a sale order directly should NOT trigger the
        # conference tier — the workflow is only activated when the
        # caller is a rep user in production (backoffice, imports and
        # tests by admin fall outside the rep-external flow).
        order = self._make_order(self.customer_c1)
        reviews = order.review_ids.filtered(
            lambda r: r.definition_id == self.conference_tier
        )
        self.assertFalse(
            reviews,
            "Admin-created orders must not trigger the conference tier "
            "— only rep users get the automatic review_ids.",
        )

    def test_rep_cannot_bypass_conference_flag_via_rpc(self):
        # A rep must not be able to sidestep the conference tier by
        # shipping ``tr_rep_conference_required=False`` in the
        # create vals. The override forces the flag to True when the
        # caller is in the rep group, regardless of what the vals
        # say.
        order = (
            self.env["sale.order"]
            .with_user(self.user_u1)
            .create(
                {
                    "partner_id": self.customer_c1.id,
                    "tr_rep_conference_required": False,
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
        )
        self.assertTrue(
            order.tr_rep_conference_required,
            "The create() override must force the flag to True for "
            "rep users even when False is explicitly passed in the "
            "vals — otherwise the tier can be bypassed via RPC.",
        )
        reviews = order.review_ids.filtered(
            lambda r: r.definition_id == self.conference_tier
        )
        self.assertTrue(
            reviews,
            "Conference tier review must still be created after the " "forced flag.",
        )

    def test_conference_review_not_created_for_non_rep_order(self):
        # ``customer_c3`` has no agent. Even if a rep could target
        # them (they can't, but we simulate by admin here), the
        # tier's ``definition_domain`` rejects the order. This
        # covers the domain guard directly.
        order = self._make_order(self.customer_c3)
        order.sudo().request_validation()
        reviews = order.review_ids.filtered(
            lambda r: r.definition_id == self.conference_tier
        )
        self.assertFalse(
            reviews,
            "Conference tier must not trigger on orders without a "
            "sales_rep_partner_id snapshot even when request_validation "
            "is called explicitly.",
        )

    def test_checker_can_approve_conference_review(self):
        order = self._make_rep_order(self.customer_c1)
        order.with_user(self.checker_user).validate_tier()
        self.assertEqual(
            order.validation_status,
            "validated",
            "After the checker approves the single pending tier, the "
            "order should reach validated state.",
        )

    # ------------------------------------------------------------------
    # Print block override
    # ------------------------------------------------------------------

    def _render_report_values(self, order):
        # Thin wrapper to keep the test readable — invokes the
        # AbstractModel's ``_get_report_values`` directly with the
        # order's id, mirroring what ``report.sale.order.render``
        # would feed it in production.
        return self.env["report.sale.report_saleorder"]._get_report_values(order.ids)

    def test_print_allowed_when_only_conference_pending(self):
        order = self._make_rep_order(self.customer_c1)
        # No discount on the order: discount_approval_level stays at
        # 'none', so only the conference tier is pending.
        result = self._render_report_values(order)
        self.assertIn("docs", result)
        self.assertEqual(result["doc_model"], "sale.order")

    def test_print_blocked_when_other_tier_pending(self):
        # Core assertion of PR 6b: print block stays on when there's
        # any pending review outside the conference tier. Uses
        # injected reviews on an admin-owned order to avoid
        # coupling this to the tr_commercial_policy discount
        # compute chain (which would require full sales_profile
        # fixture setup and recompute recurse).
        order = self._make_admin_order_with_injected_reviews(
            self.customer_c1,
            definitions=self.conference_tier + self.manager_tier,
        )
        with self.assertRaises(UserError):
            self._render_report_values(order)

    def test_print_blocked_when_conference_xmlid_missing(self):
        # Defensive branch: if the data file has not been loaded
        # (fresh install mid-upgrade, test fixture yank, tier
        # renamed by a downstream module), the override must fall
        # back to the upstream blocking behaviour instead of
        # silently letting everything through. Simulate the
        # missing tier by pointing the module constant at an
        # XMLID that does not exist in the test registry — a
        # conference review is still injected so the order has a
        # pending tier to block on.
        order = self._make_admin_order_with_injected_reviews(
            self.customer_c1,
            definitions=self.conference_tier,
        )
        with mock.patch.object(
            sale_report,
            "CONFERENCE_TIER_XMLID",
            "tr_sales_rep_access.nonexistent_tier_xmlid",
        ):
            with self.assertRaises(UserError):
                self._render_report_values(order)

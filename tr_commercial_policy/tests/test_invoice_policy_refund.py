# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import patch

from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestInvoicePolicyRefund(CommercialPolicyTestCommon):
    """Phase 6 — Refund and edge cases.

    Proves that out_refund is free from commercial policy governance:
    no validation on action_post, no price protection, no tier.
    Policy fields are present for traceability but not enforced.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()
        cls.billing_user = cls.env["res.users"].create(
            {
                "name": "Test Billing",
                "login": "test_billing_refund",
                "groups_id": [
                    (4, cls.env.ref("account.group_account_invoice").id),
                ],
            }
        )

    def _post_invoice(self, seller_discount=0.0):
        """Create and post a sale-origin invoice."""
        _order, invoice = self._create_confirmed_order_with_invoice(
            seller_discount=seller_discount,
        )
        invoice.action_post()
        return _order, invoice

    def _create_reversal(self, invoice):
        """Create a full reversal of a posted invoice."""
        wizard = (
            self.env["account.move.reversal"]
            .with_context(active_model="account.move", active_ids=invoice.ids)
            .create(
                {
                    "reason": "Test reversal",
                    "journal_id": invoice.journal_id.id,
                }
            )
        )
        action = wizard.reverse_moves()
        return self.env["account.move"].browse(action["res_id"])

    # ---------------------------------------------------------------
    # Bloco 1: Refund bypassa validação
    # ---------------------------------------------------------------

    def test_manual_out_refund_posts_without_condition(self):
        """Manual credit note without condition/profile posts freely."""
        refund = self.env["account.move"].create(
            {
                "move_type": "out_refund",
                "partner_id": self.customer.id,
            }
        )
        refund.with_context(
            tr_skip_price_protection=True,
            check_move_validity=False,
        ).write(
            {
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "quantity": 1,
                            "price_unit": 100.0,
                        },
                    )
                ]
            }
        )
        refund.action_post()
        self.assertEqual(refund.state, "posted")

    def test_out_refund_action_post_skips_policy_check(self):
        """Spy proves _check_invoice_policy is NOT called for out_refund."""
        refund = self.env["account.move"].create(
            {
                "move_type": "out_refund",
                "partner_id": self.customer.id,
            }
        )
        refund.with_context(
            tr_skip_price_protection=True,
            check_move_validity=False,
        ).write(
            {
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "quantity": 1,
                            "price_unit": 100.0,
                        },
                    )
                ]
            }
        )
        with patch.object(
            type(refund), "_check_invoice_policy", wraps=refund._check_invoice_policy
        ) as spy:
            refund.action_post()
            spy.assert_not_called()

    def test_sale_origin_refund_skips_policy_check(self):
        """Reversal of posted invoice: _check_invoice_policy NOT called."""
        _order, invoice = self._post_invoice()
        refund = self._create_reversal(invoice)
        with patch.object(
            type(refund), "_check_invoice_policy", wraps=refund._check_invoice_policy
        ) as spy:
            refund.action_post()
            spy.assert_not_called()

    def test_out_refund_posts_with_snapshot_divergence(self):
        """Refund with tampered snapshot still posts (no validation)."""
        _order, invoice = self._post_invoice()
        refund = self._create_reversal(invoice)
        # Tamper a policy field — should not matter for refund
        refund_line = refund.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        refund_line.with_context(
            tr_skip_price_protection=True, check_move_validity=False
        ).write({"base_price": 999.0})
        refund.action_post()
        self.assertEqual(refund.state, "posted")

    # ---------------------------------------------------------------
    # Bloco 2: Edição livre
    # ---------------------------------------------------------------

    def test_out_refund_allows_discount_edit(self):
        """out_refund: discount editable freely (no write guard)."""
        refund = self.env["account.move"].create(
            {
                "move_type": "out_refund",
                "partner_id": self.customer.id,
            }
        )
        refund.with_context(
            tr_skip_price_protection=True,
            check_move_validity=False,
        ).write(
            {
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "quantity": 1,
                            "price_unit": 100.0,
                        },
                    )
                ]
            }
        )
        line = refund.invoice_line_ids.filtered(
            lambda inv_line: inv_line.display_type == "product"
        )[:1]
        line.with_user(self.billing_user).with_context(check_move_validity=False).write(
            {"discount": 25.0}
        )
        self.assertAlmostEqual(line.discount, 25.0)

    # ---------------------------------------------------------------
    # Bloco 3: Rastreabilidade na reversal
    # ---------------------------------------------------------------

    def test_reverse_copies_policy_header_fields(self):
        """Reversal preserves header policy fields for traceability."""
        _order, invoice = self._post_invoice()
        refund = self._create_reversal(invoice)
        self.assertEqual(
            refund.commercial_condition_id, invoice.commercial_condition_id
        )
        self.assertEqual(refund.sales_profile_id, invoice.sales_profile_id)
        self.assertAlmostEqual(
            refund.tr_cash_discount, invoice.tr_cash_discount
        )
        self.assertAlmostEqual(refund.tr_fob_discount, invoice.tr_fob_discount)
        self.assertAlmostEqual(
            refund.tr_contractual_return, invoice.tr_contractual_return
        )

    def test_reverse_copies_policy_line_snapshot(self):
        """Reversal preserves line-level policy fields."""
        _order, invoice = self._post_invoice()
        refund = self._create_reversal(invoice)
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        ref_line = refund.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        self.assertAlmostEqual(ref_line.seller_discount, inv_line.seller_discount)
        self.assertAlmostEqual(ref_line.extra_discount, inv_line.extra_discount)
        self.assertAlmostEqual(ref_line.base_price, inv_line.base_price)
        self.assertAlmostEqual(ref_line.reference_price, inv_line.reference_price)
        self.assertAlmostEqual(ref_line.commission_rate, inv_line.commission_rate)

    def test_reverse_keeps_agent_snapshot(self):
        """Reversal preserves agent_ids and commission_id."""
        _order, invoice = self._post_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        orig_agents = inv_line.agent_ids
        refund = self._create_reversal(invoice)
        ref_line = refund.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        # Same number of agents with same commission
        self.assertEqual(len(ref_line.agent_ids), len(orig_agents))
        for orig, ref in zip(
            orig_agents.sorted("agent_id"),
            ref_line.agent_ids.sorted("agent_id"),
        ):
            self.assertEqual(ref.agent_id, orig.agent_id)
            self.assertEqual(ref.commission_id, orig.commission_id)

    def test_reverse_commission_amount_is_negative(self):
        """Reversal commission amounts are negative (account_commission)."""
        _order, invoice = self._post_invoice()
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        refund = self._create_reversal(invoice)
        ref_line = refund.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        for ref_agent in ref_line.agent_ids:
            orig_agent = inv_line.agent_ids.filtered(
                lambda agent: agent.agent_id == ref_agent.agent_id
            )[:1]
            # Refund amount should be negative of original (or both zero)
            self.assertAlmostEqual(ref_agent.amount, -orig_agent.amount)

    # ---------------------------------------------------------------
    # Bloco 4: Edge cases
    # ---------------------------------------------------------------

    def test_manual_credit_note_no_profile_revalidation(self):
        """Manual credit note with values outside profile posts freely."""
        refund = self.env["account.move"].create(
            {
                "move_type": "out_refund",
                "partner_id": self.customer.id,
                "tr_cash_discount": 99.0,  # way above any profile limit
            }
        )
        refund.with_context(
            tr_skip_price_protection=True,
            check_move_validity=False,
        ).write(
            {
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product_a.id,
                            "quantity": 1,
                            "price_unit": 100.0,
                        },
                    )
                ]
            }
        )
        refund.action_post()
        self.assertEqual(refund.state, "posted")

    def test_unmanaged_commission_warning_false_on_refund(self):
        """unmanaged_commission_warning is False for out_refund."""
        _order, invoice = self._post_invoice()
        refund = self._create_reversal(invoice)
        self.assertFalse(refund.unmanaged_commission_warning)

    def test_resync_keeps_settled_agent(self):
        """Resync does not remove extra agent when settled=True."""
        _order, invoice = self._create_confirmed_order_with_invoice(seller_discount=0.0)
        inv_line = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        )[:1]
        # Create a different agent partner (not from the sale order)
        other_agent = self.env["res.partner"].create(
            {
                "name": "Extra Agent",
                "agent": True,
                "commission_id": self.commission.id,
            }
        )
        # Add as extra agent on invoice line
        extra_agent = self.env["account.invoice.line.agent"].create(
            {
                "object_id": inv_line.id,
                "agent_id": other_agent.id,
                "commission_id": self.commission.id,
            }
        )
        agents_before = len(inv_line.agent_ids)
        # Mock settled property so the resync guard sees it as True
        with patch.object(
            type(extra_agent), "settled", new_callable=lambda: property(lambda s: True)
        ):
            invoice.action_resync_from_sale_order()
        inv_line.invalidate_recordset()
        self.assertEqual(len(inv_line.agent_ids), agents_before)
        self.assertTrue(extra_agent.exists())

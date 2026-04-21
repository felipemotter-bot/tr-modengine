# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError

from .common import SalesRepAccessTestCommon


class TestSnapshot(SalesRepAccessTestCommon):
    def test_create_populates_snapshot_from_agent(self):
        """Create with partner C1 → sales_rep_partner_id = A1."""
        order = self._make_order(self.customer_c1)
        self.assertEqual(order.sales_rep_partner_id, self.agent_a1)

    def test_create_without_agent_leaves_snapshot_empty(self):
        """Create with partner C3 (no agent) → snapshot NULL."""
        order = self._make_order(self.customer_c3)
        self.assertFalse(order.sales_rep_partner_id)

    def test_write_partner_in_draft_syncs_snapshot(self):
        """Changing partner_id in draft updates the snapshot."""
        order = self._make_order(self.customer_c1)
        order.write({"partner_id": self.customer_c2.id})
        self.assertEqual(order.sales_rep_partner_id, self.agent_a2)

    def test_write_partner_to_no_agent_clears_snapshot(self):
        """Changing partner_id to a customer without agent clears snapshot."""
        order = self._make_order(self.customer_c1)
        order.write({"partner_id": self.customer_c3.id})
        self.assertFalse(order.sales_rep_partner_id)

    def test_create_with_divergent_snapshot_raises(self):
        """Explicit sales_rep_partner_id divergent from partner's agent fails."""
        with self.assertRaises(ValidationError):
            self.env["sale.order"].create(
                {
                    "partner_id": self.customer_c1.id,
                    "sales_rep_partner_id": self.agent_a2.id,
                }
            )

    def test_write_divergent_snapshot_in_draft_raises(self):
        """write({'sales_rep_partner_id': other_agent}) in draft raises."""
        order = self._make_order(self.customer_c1)
        with self.assertRaises(ValidationError):
            order.write({"sales_rep_partner_id": self.agent_a2.id})

    def test_write_snapshot_after_confirm_raises(self):
        """Snapshot is immutable once state is not draft."""
        order = self._make_order(self.customer_c1)
        order.action_confirm()
        with self.assertRaises(UserError):
            order.write({"sales_rep_partner_id": self.agent_a1.id})

    def test_write_partner_after_confirm_raises(self):
        """Indirect snapshot change via partner_id is blocked after draft."""
        order = self._make_order(self.customer_c1)
        order.action_confirm()
        with self.assertRaises(UserError):
            order.write({"partner_id": self.customer_c2.id})

    def test_write_partner_on_mixed_recordset_blocks_all(self):
        """write(partner_id) on mixed draft+confirmed recordset raises.

        Prevents the shared-vals bug where syncing snapshot for the
        draft records would also be applied to confirmed ones via
        super().write().
        """
        order_draft = self._make_order(self.customer_c1)
        order_confirmed = self._make_order(self.customer_c1)
        order_confirmed.action_confirm()
        mixed = order_draft | order_confirmed
        with self.assertRaises(UserError):
            mixed.write({"partner_id": self.customer_c2.id})
        # The draft record's snapshot must not have been touched.
        self.assertEqual(order_draft.sales_rep_partner_id, self.agent_a1)
        self.assertEqual(order_confirmed.sales_rep_partner_id, self.agent_a1)

    def test_change_customer_agent_does_not_rewrite_history(self):
        """Changing customer's agent_ids after confirm does NOT touch snapshot."""
        order = self._make_order(self.customer_c1)
        order.action_confirm()
        # Swap the agent of C1 from A1 to A2 after confirmation.
        self.customer_c1.write({"agent_ids": [(6, 0, [self.agent_a2.id])]})
        self.assertEqual(order.sales_rep_partner_id, self.agent_a1)

    def test_copy_recomputes_snapshot_from_current_partner(self):
        """sale.order.copy() does NOT inherit snapshot; it recomputes."""
        order = self._make_order(self.customer_c1)
        order.action_confirm()
        # Change customer's agent BEFORE copying.
        self.customer_c1.write({"agent_ids": [(6, 0, [self.agent_a2.id])]})
        new_order = order.copy()
        # New order should reflect the current agent, not the old snapshot.
        self.assertEqual(new_order.sales_rep_partner_id, self.agent_a2)
        # Original order's snapshot stays untouched.
        self.assertEqual(order.sales_rep_partner_id, self.agent_a1)

    def test_prepare_invoice_propagates_snapshot(self):
        """_prepare_invoice carries sales_rep_partner_id onto the invoice.

        Tests the override directly (not via _create_invoices) because
        l10n_br_sale._create_invoices requires fiscal_operation_id +
        fiscal_operation_line_id which are not set up in this test.
        """
        order = self._make_order(self.customer_c1)
        order.action_confirm()
        invoice_vals = order._prepare_invoice()
        self.assertEqual(invoice_vals.get("sales_rep_partner_id"), self.agent_a1.id)

    def test_prepare_invoice_without_agent_propagates_empty(self):
        """C3 (no agent) → invoice vals snapshot remains empty (False)."""
        order = self._make_order(self.customer_c3)
        order.action_confirm()
        invoice_vals = order._prepare_invoice()
        self.assertFalse(invoice_vals.get("sales_rep_partner_id"))

    def test_prepare_create_vals_without_partner_is_noop(self):
        """_sales_rep_prepare_create_vals with no partner_id is a no-op."""
        vals = {"name": "dummy-no-partner"}
        self.env["sale.order"]._sales_rep_prepare_create_vals(vals)
        self.assertNotIn("sales_rep_partner_id", vals)

    def test_browse_partner_with_falsy_id_returns_empty(self):
        """_sales_rep_browse_partner(False) returns an empty recordset."""
        result = self.env["sale.order"]._sales_rep_browse_partner(False)
        self.assertFalse(result)

    def test_resolve_returns_false_for_empty_partner(self):
        """_sales_rep_resolve(empty_partner) returns False."""
        empty = self.env["res.partner"].browse()
        self.assertFalse(self.env["sale.order"]._sales_rep_resolve(empty))

    def test_resolve_returns_false_for_multiple_agents(self):
        """_sales_rep_resolve returns False (with log warning) when >1 agents.

        Bypasses the ``_check_single_agent`` constraint by using a
        virtual (``new``) record — the constraint only fires on
        persisted write/create via ORM. This exercises the defensive
        branch in _sales_rep_resolve.
        """
        virtual_partner = self.env["res.partner"].new(
            {
                "agent_ids": [(6, 0, [self.agent_a1.id, self.agent_a2.id])],
            }
        )
        self.assertFalse(self.env["sale.order"]._sales_rep_resolve(virtual_partner))

    def test_write_partner_with_explicit_empty_snapshot_raises(self):
        """write(partner_id=C2, sales_rep_partner_id=False) raises.

        Covers the divergence branch where an explicit empty snapshot
        is paired with a partner that has an agent — the snapshot is
        not empty because of the partner, so it is a conflict.
        """
        order = self._make_order(self.customer_c1)
        with self.assertRaises(ValidationError):
            order.write(
                {
                    "partner_id": self.customer_c2.id,
                    "sales_rep_partner_id": False,
                }
            )

    def test_write_partner_with_matching_snapshot_passes(self):
        """write(partner_id=C2, sales_rep_partner_id=A2) passes (consistent)."""
        order = self._make_order(self.customer_c1)
        order.write(
            {
                "partner_id": self.customer_c2.id,
                "sales_rep_partner_id": self.agent_a2.id,
            }
        )
        self.assertEqual(order.sales_rep_partner_id, self.agent_a2)

    def test_get_invoice_grouping_keys_includes_snapshot(self):
        """_get_invoice_grouping_keys appends sales_rep_partner_id."""
        order = self._make_order(self.customer_c1)
        keys = order._get_invoice_grouping_keys()
        self.assertIn("sales_rep_partner_id", keys)

    def test_get_invoice_grouping_keys_is_idempotent(self):
        """Consecutive calls do not duplicate the snapshot key.

        Branch True (key absent in super result): normal call.
        Branch False (key already present in super result): patched call
        where the parent class returns the key pre-populated, verifying
        our override does not append a duplicate.
        """
        order = self._make_order(self.customer_c1)

        # Branch True — key absent: override must append it exactly once.
        first = order._get_invoice_grouping_keys()
        self.assertEqual(first.count("sales_rep_partner_id"), 1)

        # Branch False — key already present in super() result.
        # Patch the first ancestor that defines the method so it returns
        # a list pre-populated with the key; our override must be a no-op
        # (no duplication).
        SaleOrderCls = type(order)
        parent_cls = next(
            c
            for c in SaleOrderCls.__mro__[1:]
            if "_get_invoice_grouping_keys" in c.__dict__
        )
        # Patch the parent to always return a list that pre-contains the key.
        # This forces the False branch of the `if key not in res` guard in
        # our override, verifying no duplicate is appended.
        with patch.object(
            parent_cls,
            "_get_invoice_grouping_keys",
            lambda self_inner: ["sales_rep_partner_id"],
        ):
            second = order._get_invoice_grouping_keys()
        self.assertEqual(second.count("sales_rep_partner_id"), 1)

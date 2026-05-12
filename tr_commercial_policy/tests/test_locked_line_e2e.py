# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""End-to-end tests for Locked Lines.

Mirror the style of ``test_end_to_end_pricing.py``: real
``_apply_condition_to_line`` flow (simulating the UI onchange) plus
invoicing, exercising the full chain from the director cadastrating
a locked line to the rep placing an order, the snapshots being
propagated, and the invoice inheriting them.
"""

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestLockedLineE2E(CommercialPolicyTestCommon):
    """Full-flow scenarios for locked lines."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()
        # Predictable pricelist prices so price_unit assertions are exact.
        cls.env["product.pricelist.item"].create(
            {
                "pricelist_id": cls.pricelist.id,
                "applied_on": "1_product",
                "product_tmpl_id": cls.product_template_a.id,
                "compute_price": "fixed",
                "fixed_price": 100.0,
            }
        )
        cls.env["product.pricelist.item"].create(
            {
                "pricelist_id": cls.pricelist.id,
                "applied_on": "1_product",
                "product_tmpl_id": cls.product_template_b.id,
                "compute_price": "fixed",
                "fixed_price": 200.0,
            }
        )

    def _create_locked(self, **vals):
        defaults = {
            "condition_id": self.condition.id,
            "applied_on": "product_template",
            "product_tmpl_id": self.product_template_a.id,
            "seller_discount": 5.0,
            "extra_discount": 0.0,
            "fixed_commission_rate": 0.0,
        }
        defaults.update(vals)
        return (
            self.env["partner.commercial.condition.locked.line"]
            .with_user(self.director_user)
            .create(defaults)
        )

    def _create_line_with_condition(self, order, product=None, qty=1):
        line = self._create_order_line(order, product=product, qty=qty)
        order._apply_condition_to_line(line, order.commercial_condition_id)
        return line

    def test_full_flow_director_cadastra_rep_creates_order_invoice_inherits(self):
        """E2E-1: director cadastra → rep cria pedido → preço/comissão →
        edição bloqueada → manager override → fatura herda snapshots."""
        # 1. Director cadastra locked line.
        locked = self._create_locked(
            seller_discount=5.0,
            fixed_commission_rate=0.0,
        )

        # 2. Rep cria pedido (passa pelo fluxo de _apply_condition_to_line).
        order = self._create_order()
        line = self._create_line_with_condition(order, product=self.product_a)

        # 3. Snapshot populado + preço aplicado.
        self.assertEqual(line.locked_line_id, locked)
        self.assertAlmostEqual(line.seller_discount, 5.0)
        self.assertAlmostEqual(line.locked_fixed_commission_rate, 0.0)
        self.assertAlmostEqual(line._get_policy_commission_rate(), 0.0)

        # 4. Rep tenta editar seller_discount → bloqueado.
        with self.assertRaises(ValidationError):
            line.with_user(self.salesperson).write({"seller_discount": 20.0})

        # 5. Manager faz override pontual (passa).
        line.with_user(self.manager_user).write({"seller_discount": 8.0})
        self.assertAlmostEqual(line.seller_discount, 8.0)
        # Snapshot M2O permanece intacto — override é pontual.
        self.assertEqual(line.locked_line_id, locked)

        # 6. Fatura herda os snapshots.
        invoice_vals = line._prepare_invoice_line()
        self.assertEqual(invoice_vals.get("locked_line_id"), locked.id)
        self.assertAlmostEqual(invoice_vals.get("locked_fixed_commission_rate"), 0.0)

    def test_director_edits_locked_after_order_does_not_affect_snapshot(self):
        """E2E-2: snapshot Float preserva comissão original."""
        locked = self._create_locked(fixed_commission_rate=2.0)
        order = self._create_order()
        line = self._create_line_with_condition(order, product=self.product_a)
        self.assertAlmostEqual(line.locked_fixed_commission_rate, 2.0)

        # Diretor altera locked depois.
        locked.with_user(self.director_user).write({"fixed_commission_rate": 7.0})

        # Snapshot na linha existente NÃO muda.
        self.assertAlmostEqual(line.locked_fixed_commission_rate, 2.0)
        self.assertAlmostEqual(line._get_policy_commission_rate(), 2.0)

    def test_archive_locked_used_unlink_blocked_archive_works(self):
        """E2E-3: unlink blocked when locked is in use; archive stops
        future application without affecting past orders."""
        locked = self._create_locked()
        order = self._create_order()
        line = self._create_line_with_condition(order, product=self.product_a)
        self.assertEqual(line.locked_line_id, locked)

        # Unlink barred by ondelete=restrict on snapshot.
        with self.assertRaises(Exception):
            locked.with_user(self.director_user).unlink()

        # Archive works.
        locked.with_user(self.director_user).active = False

        # Old line still references the locked record (snapshot intact).
        self.assertEqual(line.locked_line_id, locked)
        self.assertAlmostEqual(line.seller_discount, 5.0)

        # New order on same product no longer resolves the archived locked.
        new_order = self._create_order()
        new_line = self._create_line_with_condition(new_order, product=self.product_a)
        self.assertFalse(
            new_line.locked_line_id,
            "Archived locked must not apply to new orders",
        )

    def test_coexistence_variant_locked_template_regular_in_same_order(self):
        """E2E-4: locked variant + regular template = two sources in one
        order, resolution picks the right one per product."""
        # Regular template (fallback for variants other than product_a).
        self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product_template",
                "product_tmpl_id": self.product_template_a.id,
                "seller_discount": 3.0,
            }
        )
        # Locked variant — narrower scope, allowed coexistence.
        locked = self._create_locked(
            applied_on="product",
            product_id=self.product_a.id,
            product_tmpl_id=False,
            seller_discount=7.0,
            fixed_commission_rate=1.0,
        )

        order = self._create_order()
        # product_a → locked variant wins.
        line_variant = self._create_line_with_condition(order, product=self.product_a)
        self.assertEqual(line_variant.locked_line_id, locked)
        self.assertAlmostEqual(line_variant.seller_discount, 7.0)
        self.assertAlmostEqual(line_variant.locked_fixed_commission_rate, 1.0)

        # product_b → no rule covers it; falls to general (condition
        # seller_discount = 5%).
        line_other = self._create_line_with_condition(order, product=self.product_b)
        self.assertFalse(line_other.locked_line_id)
        self.assertAlmostEqual(line_other.seller_discount, 5.0)

    def test_wizard_save_condition_line_blocked_under_locked(self):
        """E2E-5: rep tries to save a line under locked as condition.line →
        coexistence constraint propagates ValidationError without being
        swallowed by the wizard flow."""
        self._create_locked()
        order = self._create_order()
        line = self._create_line_with_condition(order, product=self.product_a)
        self.assertTrue(line.locked_line_id)

        # Attempt to create the conflicting regular condition.line directly
        # (mimics what the wizard would do at save time).
        with self.assertRaises(ValidationError):
            self.env["partner.commercial.condition.line"].with_user(
                self.salesperson
            ).create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product",
                    "product_id": self.product_a.id,
                    "seller_discount": 99.0,
                }
            )

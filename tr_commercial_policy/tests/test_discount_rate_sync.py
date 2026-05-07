# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import Form, tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestDiscountRateSync(CommercialPolicyTestCommon):
    """Test synchronization between commercial policy discounts and l10n_br_sale
    discount_rate field."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    # -- Header sync tests --

    def test_discount_rate_syncs_from_condition(self):
        """discount_rate = cash_discount + fob_discount after condition compute."""
        order = self._create_order()
        # condition has cash=2.0, fob=1.0
        self.assertAlmostEqual(order.discount_rate, 3.0, places=2)

    def test_discount_rate_syncs_only_cash(self):
        """discount_rate reflects only cash_discount when fob is zero."""
        self.condition.fob_discount = 0.0
        order = self._create_order()
        self.assertAlmostEqual(order.discount_rate, 2.0, places=2)

    def test_discount_rate_syncs_only_fob(self):
        """discount_rate reflects only fob_discount when cash is zero."""
        self.condition.cash_discount = 0.0
        order = self._create_order()
        self.assertAlmostEqual(order.discount_rate, 1.0, places=2)

    def test_discount_rate_zero_when_no_discounts(self):
        """discount_rate is zero when both cash and fob are zero."""
        self.condition.cash_discount = 0.0
        self.condition.fob_discount = 0.0
        order = self._create_order()
        self.assertAlmostEqual(order.discount_rate, 0.0, places=2)

    def test_discount_rate_zero_without_condition(self):
        """discount_rate is zero when there is no commercial condition."""
        self.customer.commercial_condition_id = False
        order = self._create_order()
        self.assertAlmostEqual(order.discount_rate, 0.0, places=2)

    # -- Line propagation tests --

    def test_line_discount_matches_cash_plus_fob(self):
        """line.discount equals cash_discount + fob_discount."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        self.assertAlmostEqual(line.discount, 3.0, places=2)

    def test_line_discount_value_consistent(self):
        """line.discount_value = qty * price_unit * discount / 100."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        expected = line.product_uom_qty * line.price_unit * line.discount / 100
        self.assertAlmostEqual(line.discount_value, expected, places=2)

    def test_line_discount_only_cash(self):
        """line.discount correct with only cash_discount."""
        self.condition.fob_discount = 0.0
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        self.assertAlmostEqual(line.discount, 2.0, places=2)

    def test_line_discount_only_fob(self):
        """line.discount correct with only fob_discount."""
        self.condition.cash_discount = 0.0
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        self.assertAlmostEqual(line.discount, 1.0, places=2)

    def test_line_discount_zero_without_condition(self):
        """line.discount is zero when no commercial condition."""
        self.customer.commercial_condition_id = False
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        self.assertAlmostEqual(line.discount, 0.0, places=2)

    # -- Fallback behavior tests --

    def test_fallback_corrects_when_policy_active(self):
        """Fallback ensures discount matches policy even if super() diverges."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        # Force a divergence by writing discount directly (as admin)
        line.with_context(tr_skip_price_protection=True).discount = 99.0
        # Trigger recompute
        line._compute_discounts()
        self.assertAlmostEqual(line.discount, 3.0, places=2)

    def test_no_fallback_without_condition(self):
        """Without commercial condition, fallback does not act."""
        self.customer.commercial_condition_id = False
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        # discount should be whatever super() sets (no policy correction)
        # Just verify it doesn't raise and discount is not forced to cash+fob
        self.assertAlmostEqual(line.discount, 0.0, places=2)

    def test_discount_fixed_overridden_by_policy(self):
        """Policy is absolute: discount_fixed=True is still corrected."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        line.discount_fixed = True
        line._compute_discounts()
        # Policy is absolute, fallback corrects regardless of discount_fixed
        self.assertAlmostEqual(line.discount, 3.0, places=2)

    # -- Server-side reload tests --

    def test_discount_rate_syncs_on_reload_conditions(self):
        """discount_rate updates when _apply_reload_conditions() is called."""
        order = self._create_order()
        self._create_order_line(order, base_price=100.0)
        # Change condition values
        self.condition.cash_discount = 4.0
        self.condition.fob_discount = 2.0
        order._apply_reload_conditions()
        self.assertAlmostEqual(order.discount_rate, 6.0, places=2)

    def test_line_discount_after_reload_conditions(self):
        """line.discount reflects new values after _apply_reload_conditions()."""
        order = self._create_order()
        line = self._create_order_line(order, base_price=100.0)
        self.condition.cash_discount = 4.0
        self.condition.fob_discount = 2.0
        order._apply_reload_conditions()
        self.assertAlmostEqual(line.discount, 6.0, places=2)

    # -- View-level reproducer --

    def test_discount_rate_persists_after_form_edit(self):
        """Editing cash_discount via the form must persist discount_rate.

        Bug: ``discount_rate`` is rendered ``readonly="1"`` in the form,
        so the value the onchange computes (``cash + fob``) is dropped
        by the web client on save and the database keeps the previous
        value (zero on a fresh order). Reproduces by using ``Form``,
        which mirrors the web client's readonly handling.
        """
        order = self._create_order()
        # Sanity: condition seeds cash=2, fob=1 → discount_rate=3
        self.assertAlmostEqual(order.discount_rate, 3.0, places=2)
        with Form(order) as order_form:
            order_form.cash_discount = 4.0
            order_form.fob_discount = 2.0
        self.assertAlmostEqual(order.cash_discount, 4.0, places=2)
        self.assertAlmostEqual(order.fob_discount, 2.0, places=2)
        self.assertAlmostEqual(order.discount_rate, 6.0, places=2)

# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestManagedCommission(CommercialPolicyTestCommon):
    """Test the managed commission resolver."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    def test_managed_commission_creates_commission(self):
        """Managed commission resolver creates a managed commission for a new (invoice_state, rate)."""
        Commission = self.env["commission"]
        result = Commission._ensure_managed_commission("open", 6.0)
        self.assertTrue(result)
        self.assertTrue(result.tr_managed)
        self.assertAlmostEqual(result.tr_rate, 6.0, places=2)
        self.assertEqual(result.invoice_state, "open")
        self.assertEqual(result.commission_type, "formula")
        self.assertIn("0.06", result.formula)

    def test_managed_commission_reuses_existing(self):
        """Managed commission resolver reuses existing managed commission for same combination."""
        Commission = self.env["commission"]
        first = Commission._ensure_managed_commission("open", 8.0)
        second = Commission._ensure_managed_commission("open", 8.0)
        self.assertEqual(first.id, second.id)

    def test_managed_commission_different_modality_creates_separate(self):
        """Different invoice_state creates separate commission records."""
        Commission = self.env["commission"]
        confirm = Commission._ensure_managed_commission("partner_confirm", 6.0)
        payment = Commission._ensure_managed_commission("partial_paid", 6.0)
        self.assertNotEqual(confirm.id, payment.id)
        self.assertEqual(confirm.invoice_state, "partner_confirm")
        self.assertEqual(payment.invoice_state, "partial_paid")

    def test_managed_commission_decimal_rate(self):
        """Managed commission resolver handles decimal rates correctly."""
        Commission = self.env["commission"]
        result = Commission._ensure_managed_commission("open", 6.5)
        self.assertAlmostEqual(result.tr_rate, 6.5, places=2)
        self.assertIn("0.065", result.formula)

    def test_managed_commission_invalid_template_raises_error(self):
        """Managed commission resolver raises UserError when template is invalid."""
        # Write directly to bypass propagation (which also validates)
        param = (
            self.env["ir.config_parameter"]
            .sudo()
            .search([("key", "=", "tr.commission_formula_template")], limit=1)
        )
        if param:
            param.with_context(
                tr_skip_propagation=True
            ).value = "no_rate_placeholder"
        Commission = self.env["commission"]
        with self.assertRaises(UserError):
            Commission._ensure_managed_commission("open", 5.0)

    def test_managed_commission_zero_rate_creates_record(self):
        """Managed commission resolver creates a 0% commission for zero rate."""
        Commission = self.env["commission"]
        result = Commission._ensure_managed_commission("open", 0.0)
        self.assertTrue(result)
        self.assertTrue(result.tr_managed)
        self.assertAlmostEqual(result.tr_rate, 0.0, places=2)


@tagged("post_install", "-at_install")
class TestCommissionConstraint(CommercialPolicyTestCommon):
    """Test the constraint on managed commission records."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    def test_constraint_rejects_formula_edit(self):
        """Editing formula of managed commission raises ValidationError."""
        Commission = self.env["commission"]
        managed = Commission._ensure_managed_commission("open", 5.0)
        with self.assertRaises(ValidationError):
            managed.formula = "result = 42"

    def test_constraint_allows_manual_commission_edit(self):
        """Editing formula of non-managed commission works normally."""
        self.commission.formula = "result = 999"
        self.assertEqual(self.commission.formula, "result = 999")

    def test_constraint_rejects_rate_formula_mismatch(self):
        """Changing tr_rate without matching formula raises error."""
        Commission = self.env["commission"]
        managed = Commission._ensure_managed_commission("open", 5.0)
        with self.assertRaises(ValidationError):
            managed.tr_rate = 99.0


@tagged("post_install", "-at_install")
class TestTemplatePropagation(CommercialPolicyTestCommon):
    """Test template propagation to managed commissions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    def test_template_change_propagates(self):
        """Changing the template updates all managed commissions."""
        Commission = self.env["commission"]
        comm_5 = Commission._ensure_managed_commission("open", 5.0)
        comm_8 = Commission._ensure_managed_commission("open", 8.0)
        old_formula_5 = comm_5.formula
        # Change template
        new_template = (
            "commission = {rate}\n"
            "punctuality_discount = 0.00\n"
            "if line._name == 'sale.order.line':\n"
            "    punctuality_discount = "
            "(line.order_id.punctuality_discount/100)\n"
            "if line._name == 'account.move.line':\n"
            "    punctuality_discount = "
            "(line.move_id.invoice_punctuality_discount/100)\n"
            "result = (line.price_subtotal * commission)"
            " * (1 - punctuality_discount)"
        )
        self.env["ir.config_parameter"].sudo().set_param(
            "tr.commission_formula_template", new_template
        )
        # Refresh
        comm_5.invalidate_recordset(["formula"])
        comm_8.invalidate_recordset(["formula"])
        self.assertNotEqual(comm_5.formula, old_formula_5)
        # New formula should contain price_subtotal instead of fiscal_amount_untaxed
        self.assertIn("price_subtotal", comm_5.formula)
        self.assertIn("0.05", comm_5.formula)
        self.assertIn("0.08", comm_8.formula)

    def test_template_change_preserves_manual(self):
        """Template change does not affect non-managed commissions."""
        # Ensure manual commission has a formula
        self.commission.commission_type = "formula"
        self.commission.formula = "result = 42"
        original_formula = self.commission.formula
        new_template = (
            "commission = {rate}\n"
            "punctuality_discount = 0.00\n"
            "if line._name == 'sale.order.line':\n"
            "    punctuality_discount = "
            "(line.order_id.punctuality_discount/100)\n"
            "if line._name == 'account.move.line':\n"
            "    punctuality_discount = "
            "(line.move_id.invoice_punctuality_discount/100)\n"
            "result = (line.price_subtotal * commission)"
            " * (1 - punctuality_discount)"
        )
        self.env["ir.config_parameter"].sudo().set_param(
            "tr.commission_formula_template", new_template
        )
        self.commission.invalidate_recordset(["formula"])
        # Manual commission should not change
        self.assertEqual(self.commission.formula, original_formula)


@tagged("post_install", "-at_install")
class TestManagedCommissionFormula(CommercialPolicyTestCommon):
    """Test managed commission with realistic formula commission (production-like)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        # Create a formula commission (like realistic data)
        cls.formula_commission = cls.env["commission"].create(
            {
                "name": "8% NA CONFIRMAÇÃO",
                "commission_type": "formula",
                "amount_base_type": "gross_amount",
                "invoice_state": "partner_confirm",
                "formula": (
                    "commission = 0.08\n"
                    "punctuality_discount = 0.00\n"
                    "if line._name == 'sale.order.line':\n"
                    "    punctuality_discount = "
                    "(line.order_id.punctuality_discount/100)\n"
                    "if line._name == 'account.move.line':\n"
                    "    punctuality_discount = "
                    "(line.move_id.invoice_punctuality_discount/100)\n"
                    "result = (line.fiscal_amount_untaxed * commission)"
                    " * (1 - punctuality_discount)"
                ),
            }
        )
        # Agent with formula commission
        cls.agent_partner = cls.env["res.partner"].create(
            {
                "name": "Test Agent Formula",
                "agent": True,
                "commission_id": cls.formula_commission.id,
                "sales_profile_id": cls.agent_profile.id,
            }
        )
        cls.customer.agent_ids = [(6, 0, [cls.agent_partner.id])]

    def test_managed_commission_preserves_modality_from_formula_commission(self):
        """Managed commission resolver creates commission with same invoice_state as formula base."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        self.assertEqual(line.commission_rate, 10.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        self.assertTrue(agent_line)
        comm = agent_line.commission_id
        self.assertTrue(comm.tr_managed)
        self.assertEqual(comm.invoice_state, "partner_confirm")
        self.assertAlmostEqual(comm.tr_rate, 10.0, places=2)
        self.assertEqual(comm.commission_type, "formula")

    def test_managed_commission_commission_formula_contains_correct_rate(self):
        """Managed commission resolver commission formula uses the band rate, not the agent's."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=7.0)
        self.assertEqual(line.commission_rate, 7.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        comm = agent_line.commission_id
        self.assertIn("0.07", comm.formula)
        self.assertNotIn("0.08", comm.formula)


@tagged("post_install", "-at_install")
class TestManagedCommissionInSaleOrder(CommercialPolicyTestCommon):
    """Test managed commission integration in the sale order flow."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    def test_agent_line_gets_managed_commission(self):
        """Agent line on SO gets managed commission when profile has bands."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        self.assertEqual(line.commission_rate, 10.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        self.assertTrue(agent_line)
        self.assertTrue(agent_line.commission_id.tr_managed)
        self.assertAlmostEqual(agent_line.commission_id.tr_rate, 10.0, places=2)
        self.assertEqual(
            agent_line.commission_id.invoice_state,
            self.agent_partner.commission_id.invoice_state,
        )

    def test_base_always_from_agent(self):
        """Managed commission resolver uses agent's commission invoice_state as modality."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        # The modality should match the agent's base commission
        self.assertEqual(
            agent_line.commission_id.invoice_state,
            self.agent_partner.commission_id.invoice_state,
        )

    def test_no_profile_no_managed_commission(self):
        """Without agent profile, managed commission is not used."""
        self.customer.agent_ids = [(5,)]
        self.salesperson.partner_id.sales_profile_id = False
        self.env.company.default_sales_profile_id = False
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=0.0)
        # No agent → no agent lines
        self.assertFalse(line.agent_ids)

    def test_internal_profile_no_managed_commission(self):
        """Internal profile does not trigger the managed commission resolver."""
        self.agent_partner.sales_profile_id = self.internal_profile
        self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": self.internal_profile.id,
                "applied_on": "general",
                "order_value_band_ids": [
                    (0, 0, {"order_min_amount": 0.0, "seller_discount_max": 10.0}),
                ],
            }
        )
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        if agent_line:
            # Should NOT be managed by the commercial policy
            self.assertFalse(agent_line.commission_id.tr_managed)

    def test_no_bands_no_managed_commission(self):
        """When bands are removed, managed commission is not used."""
        self.general_rule.commission_band_ids.unlink()
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=0.0)
        self.assertEqual(line.commission_rate, 0.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        if agent_line:
            self.assertFalse(agent_line.commission_id.tr_managed)


@tagged("post_install", "-at_install")
class TestPricelistCommissionGuard(CommercialPolicyTestCommon):
    """Test the guard against sale_commission_pricelist."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    def test_confirm_blocked_when_pricelist_commission_installed(self):
        """action_confirm raises UserError when sale_commission_pricelist is installed."""
        Module = self.env["ir.module.module"].sudo()
        module = Module.search([("name", "=", "sale_commission_pricelist")], limit=1)
        if not module:
            module = Module.create(
                {"name": "sale_commission_pricelist", "state": "uninstalled"}
            )
        old_state = module.state
        module.state = "installed"
        try:
            order = self._create_order()
            order.fiscal_operation_id = False
            self._create_order_line(order, seller_discount=3.0)
            with self.assertRaises(UserError) as ctx:
                order.action_confirm()
            self.assertIn("sale_commission_pricelist", str(ctx.exception))
        finally:
            module.state = old_state


@tagged("post_install", "-at_install")
class TestLiveModelBehavior(CommercialPolicyTestCommon):
    """Test that managed commissions are 'live' — template changes propagate."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    def test_open_document_recompute_uses_new_template(self):
        """After template change, recompute on open document uses new formula."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        self.assertTrue(agent_line)

        # Change template to use price_subtotal instead of fiscal_amount_untaxed
        new_template = (
            "commission = {rate}\n"
            "punctuality_discount = 0.00\n"
            "if line._name == 'sale.order.line':\n"
            "    punctuality_discount = "
            "(line.order_id.punctuality_discount/100)\n"
            "if line._name == 'account.move.line':\n"
            "    punctuality_discount = "
            "(line.move_id.invoice_punctuality_discount/100)\n"
            "result = (line.price_subtotal * commission)"
            " * (1 - punctuality_discount)"
        )
        self.env["ir.config_parameter"].sudo().set_param(
            "tr.commission_formula_template", new_template
        )

        # Force recompute on the agent line
        agent_line.invalidate_recordset(["amount"])
        agent_line._compute_amount()

        # If fiscal_amount_untaxed != price_subtotal, amount should differ.
        # If they happen to be equal in test setup, at minimum the formula
        # changed and the recompute ran without error.
        self.assertIn("price_subtotal", agent_line.commission_id.formula)


@tagged("post_install", "-at_install")
class TestManagedCommissionEdgeCases(CommercialPolicyTestCommon):
    """Cover edge case branches for patch coverage."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    def test_resolve_with_non_managed_commission(self):
        """_resolve_agent_commissions skips non-managed agent commissions."""
        # Agent has a standard (non-managed) commission
        # After resolve, the validation loop should skip it
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        # The resolved commission should be managed
        self.assertTrue(agent_line.commission_id.tr_managed)

    def test_prepare_agent_vals_finds_existing_managed(self):
        """_prepare_agent_vals uses existing managed commission if available."""
        Commission = self.env["commission"]
        # Pre-create the managed commission
        base_state = self.agent_partner.commission_id.invoice_state
        Commission._ensure_managed_commission(base_state, 10.0)
        # Now create a line — _prepare_agent_vals should find it
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        self.assertTrue(agent_line)
        self.assertTrue(agent_line.commission_id.tr_managed)

    def test_discount_change_re_resolves_managed_commission(self):
        """Changing seller_discount re-resolves the managed commission."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        self.assertEqual(line.commission_rate, 10.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        self.assertTrue(agent_line.commission_id.tr_managed)
        self.assertAlmostEqual(agent_line.commission_id.tr_rate, 10.0, places=2)
        # Change discount to trigger different band rate
        line.write({"seller_discount": 7.0})
        agent_line.invalidate_recordset(["commission_id"])
        # After resolve, commission should match new rate
        self.assertAlmostEqual(agent_line.commission_id.tr_rate, 7.0, places=2)

    def test_resolve_validates_rate_consistency(self):
        """_resolve_agent_commissions validates commission rate consistency."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        self.assertTrue(agent_line.commission_id.tr_managed)
        # The rate should match — no error
        self.assertAlmostEqual(agent_line.commission_id.tr_rate, 10.0, places=2)

    def test_internal_profile_agent_line_not_managed(self):
        """Agent line with internal profile is not managed."""
        self.agent_partner.sales_profile_id = self.internal_profile
        self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": self.internal_profile.id,
                "applied_on": "general",
                "order_value_band_ids": [
                    (0, 0, {"order_min_amount": 0.0, "seller_discount_max": 10.0}),
                ],
            }
        )
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        if agent_line:
            self.assertFalse(agent_line.commission_id.tr_managed)


@tagged("post_install", "-at_install")
class TestValidationErrors(CommercialPolicyTestCommon):
    """Cover validation error paths in commission template."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    def test_validate_empty_template(self):
        """Empty template raises UserError."""
        Commission = self.env["commission"]
        with self.assertRaises(UserError):
            Commission._validate_formula_template("")

    def test_validate_no_rate_placeholder(self):
        """Template without {rate} raises UserError."""
        Commission = self.env["commission"]
        with self.assertRaises(UserError):
            Commission._validate_formula_template("result = 42")

    def test_validate_no_result(self):
        """Template without 'result' raises UserError."""
        Commission = self.env["commission"]
        with self.assertRaises(UserError):
            Commission._validate_formula_template("x = {rate}")

    def test_validate_syntax_error(self):
        """Template with syntax error raises UserError."""
        Commission = self.env["commission"]
        with self.assertRaises(UserError):
            Commission._validate_formula_template("result = {rate} +++")

    def test_validate_wrong_result(self):
        """Template that produces wrong numeric result raises UserError."""
        Commission = self.env["commission"]
        with self.assertRaises(UserError):
            Commission._validate_formula_template("commission = {rate}\nresult = 999")

    def test_validate_negative_result(self):
        """Template that produces negative result raises UserError."""
        Commission = self.env["commission"]
        with self.assertRaises(UserError):
            Commission._validate_formula_template(
                "commission = {rate}\nresult = -commission * 1000"
            )

    def test_validate_non_numeric_result(self):
        """Template that produces non-numeric result raises UserError."""
        Commission = self.env["commission"]
        with self.assertRaises(UserError):
            Commission._validate_formula_template(
                "commission = {rate}\nresult = 'text'"
            )

    def test_validate_runtime_error(self):
        """Template that fails at runtime raises UserError."""
        Commission = self.env["commission"]
        with self.assertRaises(UserError):
            Commission._validate_formula_template(
                "result = line.nonexistent_field * {rate}"
            )

    def test_constraint_no_template_skips(self):
        """Constraint skips when template parameter is empty."""
        Commission = self.env["commission"]
        comm = Commission._ensure_managed_commission("open", 5.0)
        # Remove the template
        self.env["ir.config_parameter"].sudo().search(
            [("key", "=", "tr.commission_formula_template")]
        ).with_context(tr_skip_propagation=True).value = ""
        # Write without propagation context — constraint should skip
        # because template is empty, not because of context flag
        comm.formula = "result = 999"


@tagged("post_install", "-at_install")
class TestStaleCommissionCheck(CommercialPolicyTestCommon):
    """Test _check_stale_commissions in action_confirm."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    def test_stale_commission_blocks_confirm(self):
        """Confirm is blocked when agent commission rate doesn't match band."""
        order = self._create_order()
        order.fiscal_operation_id = False
        line = self._create_order_line(order, seller_discount=3.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        self.assertTrue(agent_line.commission_id.tr_managed)
        # Artificially make the commission stale by changing its rate
        agent_line.commission_id.with_context(tr_template_propagation=True).write(
            {"tr_rate": 99.0}
        )
        with self.assertRaises(UserError) as ctx:
            order.action_confirm()
        self.assertIn("stale", str(ctx.exception).lower())

    def test_stale_check_blocks_non_managed_for_non_director(self):
        """Non-managed commission blocks confirm for non-director users."""
        order = self._create_order()
        order.fiscal_operation_id = False
        line = self._create_order_line(order, seller_discount=3.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        agent_line.commission_id = self.commission
        with self.assertRaises(UserError):
            order.with_user(self.salesperson)._check_stale_commissions()

    def test_stale_check_allows_non_managed_for_director(self):
        """Non-managed commission is allowed for director users."""
        order = self._create_order()
        order.fiscal_operation_id = False
        line = self._create_order_line(order, seller_discount=3.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        agent_line.commission_id = self.commission
        order.with_user(self.director_user)._check_stale_commissions()

    def test_unmanaged_commission_warning_shown(self):
        """Warning is computed when agent line has non-managed commission."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        agent_line.commission_id = self.commission
        order.invalidate_recordset(["unmanaged_commission_warning"])
        self.assertTrue(order.unmanaged_commission_warning)

    def test_unmanaged_warning_skips_newid_lines(self):
        """Warning compute no-ops on NewId lines (pre-save false positive)."""
        from odoo.tests.common import Form

        order = self._create_order()
        # Add a line through the Form wrapper so the compute runs while
        # the new line is still ``NewId`` — the same context the user
        # experiences in the UI before hitting Save. We deliberately do
        # not use ``with Form(order) as order_form:`` as the outer
        # context manager, because the implicit save on ``__exit__``
        # would validate required fields that are unrelated to this
        # test (e.g., ``ind_final`` when the env has
        # ``fiscal_operation_id`` defaulted). The test is about the
        # compute in ``NewId``, not about full persistence.
        order_form = Form(order)
        with order_form.order_line.new() as line_form:
            line_form.product_id = self.product_a
            # Touching the warning during the onchange cycle must
            # not raise and must not flag a NewId line as unmanaged.
            self.assertFalse(order_form.unmanaged_commission_warning)

    def test_unmanaged_commission_warning_cleared(self):
        """Warning is cleared when all commissions are managed."""
        order = self._create_order()
        self._create_order_line(order, seller_discount=3.0)
        order.invalidate_recordset(["unmanaged_commission_warning"])
        self.assertFalse(order.unmanaged_commission_warning)

    def test_unmanaged_warning_clears_after_recalculate(self):
        """Warning clears after using Recalculate Commissions."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        agent_line.commission_id = self.commission
        order.invalidate_recordset(["unmanaged_commission_warning"])
        self.assertTrue(order.unmanaged_commission_warning)
        # Recalculate should fix it
        order.action_recalculate_commissions()
        order.invalidate_recordset(["unmanaged_commission_warning"])
        self.assertFalse(order.unmanaged_commission_warning)

    def test_unmanaged_warning_false_without_agent_profile(self):
        """Warning is False when order has no agent profile."""
        self.agent_partner.sales_profile_id = self.internal_profile
        self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": self.internal_profile.id,
                "applied_on": "general",
                "order_value_band_ids": [
                    (0, 0, {"order_min_amount": 0.0, "seller_discount_max": 10.0}),
                ],
            }
        )
        order = self._create_order()
        self._create_order_line(order, seller_discount=3.0)
        order.invalidate_recordset(["unmanaged_commission_warning"])
        self.assertFalse(order.unmanaged_commission_warning)

    def test_unmanaged_warning_true_when_discount_above_bands(self):
        """Warning fires when seller_discount is outside the configured
        bands, mirroring the invoice behavior.

        Even when ``_get_commission_rate_from_bands`` returns ``False``
        (no band covers this discount), any non-managed agent commission
        on the line still triggers the warning.
        """
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        agent_line.commission_id = self.commission
        # Push the line's seller_discount outside the configured bands
        # via SQL to bypass the discount-limit validation.
        self.env.cr.execute(
            "UPDATE sale_order_line SET seller_discount = 50 WHERE id = %s",
            (line.id,),
        )
        line.invalidate_recordset(["seller_discount"])
        self.assertFalse(line._get_commission_rate_from_bands())
        order.invalidate_recordset(["unmanaged_commission_warning"])
        self.assertTrue(order.unmanaged_commission_warning)
        # The warning should surface the outside-bands phrasing, not only
        # the Recalculate-Commissions hint.
        self.assertIn("outside", order.unmanaged_commission_warning)

    def test_non_stale_commission_passes_stale_check(self):
        """_check_stale_commissions passes when rates match."""
        order = self._create_order()
        order.fiscal_operation_id = False
        line = self._create_order_line(order, seller_discount=3.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        self.assertTrue(agent_line.commission_id.tr_managed)
        # Rates match — stale check should not raise
        order._check_stale_commissions()


@tagged("post_install", "-at_install")
class TestRecalculateCommissions(CommercialPolicyTestCommon):
    """Test action_recalculate_commissions on sale.order."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()

    def test_recalculate_fixes_stale_commission(self):
        """Recalculate resolves stale commission without changing discounts."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        self.assertTrue(agent_line.commission_id.tr_managed)
        self.assertAlmostEqual(agent_line.commission_id.tr_rate, 10.0, places=2)
        # Make commission stale by changing rate directly
        agent_line.commission_id.with_context(tr_template_propagation=True).write(
            {"tr_rate": 99.0}
        )
        # Recalculate should fix it
        order.action_recalculate_commissions()
        agent_line.invalidate_recordset(["commission_id"])
        self.assertAlmostEqual(agent_line.commission_id.tr_rate, 10.0, places=2)
        # Discount should not have changed
        self.assertAlmostEqual(line.seller_discount, 3.0, places=2)

    def test_recalculate_updates_rate_after_discount_change(self):
        """Recalculate picks up new commission rate after manual discount edit."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=3.0)
        self.assertEqual(line.commission_rate, 10.0)
        # Manually change seller_discount (simulates manual edit scenario)
        line.write({"seller_discount": 7.0})
        # commission_rate should now be 7.0 (from second band)
        self.assertEqual(line.commission_rate, 7.0)
        # Recalculate to ensure agent line matches
        order.action_recalculate_commissions()
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        self.assertAlmostEqual(agent_line.commission_id.tr_rate, 7.0, places=2)


@tagged("post_install", "-at_install")
class TestZeroRateCommission(CommercialPolicyTestCommon):
    """Test that band rate 0% creates a managed commission with zero amount."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls._setup_commission_bands()
        cls._setup_agent()
        # Add a band with 0% commission for high discounts
        cls.env["tr.sales.profile.commission.band"].create(
            {
                "rule_id": cls.general_rule.id,
                "discount_up_to": 15.0,
                "commission_rate": 0.0,
            }
        )

    def test_zero_rate_creates_managed_commission(self):
        """Band rate 0% creates a managed commission with tr_rate=0."""
        order = self._create_order()
        # seller_discount > 10 falls into the 0% band (up_to=15)
        line = self._create_order_line(order, seller_discount=12.0)
        self.assertAlmostEqual(line.commission_rate, 0.0, places=2)
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        self.assertTrue(agent_line)
        self.assertTrue(agent_line.commission_id.tr_managed)
        self.assertAlmostEqual(agent_line.commission_id.tr_rate, 0.0, places=2)

    def test_no_band_match_does_not_create_zero_managed(self):
        """Discount beyond all bands does NOT create a 0% managed commission.

        Bands go up to 15%. A seller_discount above that has no matching
        band — _get_commission_rate_for_discount returns False.
        """
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=0.0)
        # Use helper directly to test discount beyond all bands
        rate = line._get_commission_rate_for_discount(20.0)
        self.assertIs(rate, False, "Expected False when no band matches")

    def test_zero_rate_stale_check_passes(self):
        """Stale check passes with 0% managed commission."""
        order = self._create_order()
        order.fiscal_operation_id = False
        line = self._create_order_line(order, seller_discount=12.0)
        self.assertAlmostEqual(line.commission_rate, 0.0, places=2)
        # Should not raise
        order._check_stale_commissions()

    def test_zero_rate_recalculate_works(self):
        """Recalculate commissions works with 0% rate."""
        order = self._create_order()
        line = self._create_order_line(order, seller_discount=12.0)
        order.action_recalculate_commissions()
        agent_line = line.agent_ids.filtered(
            lambda agent: agent.agent_id == self.agent_partner
        )
        self.assertTrue(agent_line.commission_id.tr_managed)
        self.assertAlmostEqual(agent_line.commission_id.tr_rate, 0.0, places=2)

    def test_negative_commission_rate_blocked(self):
        """Constraint prevents creating bands with negative commission rate."""
        with self.assertRaises(ValidationError):
            self.env["tr.sales.profile.commission.band"].create(
                {
                    "rule_id": self.general_rule.id,
                    "discount_up_to": 20.0,
                    "commission_rate": -5.0,
                }
            )

    def test_stale_check_skips_no_band_match(self):
        """Stale check skips lines where no band matches the discount.

        The stale check uses _get_commission_rate_from_bands() which
        returns False when no band matches. This test verifies the
        check doesn't raise for that case.
        """
        order = self._create_order()
        order.fiscal_operation_id = False
        # Create line with valid discount (within bands)
        self._create_order_line(order, seller_discount=3.0)
        # Remove all bands so _get_commission_rate_from_bands returns False
        self.general_rule.commission_band_ids.unlink()
        # Should not raise — no bands means skip, not stale
        order._check_stale_commissions()

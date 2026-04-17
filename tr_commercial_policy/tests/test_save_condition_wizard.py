# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestSaveConditionWizard(CommercialPolicyTestCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_wizard_opens_with_differences(self):
        """Wizard creates lines only for products with different discounts."""
        order = self._create_order()
        self._create_order_line(order, seller_discount=5.0)  # same as condition
        self._create_order_line(
            order, product=self.product_b, seller_discount=8.0
        )  # different
        result = order.action_open_save_condition_wizard()
        wizard = self.env["tr.save.condition.wizard"].browse(result["res_id"])
        # Only product_b should appear (5.0 == condition general, 8.0 != 5.0)
        self.assertEqual(len(wizard.line_ids), 1)
        self.assertEqual(wizard.line_ids.product_id, self.product_b)

    def test_wizard_updates_general_discounts(self):
        """Wizard updates general cash/fob discounts when selected."""
        order = self._create_order()
        order.cash_discount = 3.0
        order.fob_discount = 2.0
        result = order.action_open_save_condition_wizard()
        wizard = self.env["tr.save.condition.wizard"].browse(result["res_id"])
        wizard.update_cash_discount = True
        wizard.update_fob_discount = True
        wizard.action_save()
        self.assertEqual(self.condition.cash_discount, 3.0)
        self.assertEqual(self.condition.fob_discount, 2.0)

    def test_wizard_does_not_update_unchecked(self):
        """Wizard does not update general discounts when not checked."""
        order = self._create_order()
        order.cash_discount = 3.0
        result = order.action_open_save_condition_wizard()
        wizard = self.env["tr.save.condition.wizard"].browse(result["res_id"])
        wizard.update_cash_discount = False
        wizard.action_save()
        self.assertEqual(self.condition.cash_discount, 2.0)  # unchanged

    def test_wizard_no_flags_skips_condition_write(self):
        """When all update flags are False, condition.write is not called."""
        order = self._create_order()
        result = order.action_open_save_condition_wizard()
        wizard = self.env["tr.save.condition.wizard"].browse(result["res_id"])
        wizard.update_cash_discount = False
        wizard.update_fob_discount = False
        wizard.update_seller_discount = False
        wizard.line_ids.ignore = True
        original_cash = self.condition.cash_discount
        original_fob = self.condition.fob_discount
        wizard.action_save()
        self.assertEqual(self.condition.cash_discount, original_cash)
        self.assertEqual(self.condition.fob_discount, original_fob)

    def test_wizard_saves_product_line_as_template(self):
        """Wizard saves a product line as template-level condition."""
        order = self._create_order()
        self._create_order_line(order, product=self.product_b, seller_discount=8.0)
        result = order.action_open_save_condition_wizard()
        wizard = self.env["tr.save.condition.wizard"].browse(result["res_id"])
        wizard.line_ids.save_as = "template"
        wizard.action_save()
        cond_line = self.condition.line_ids.filtered(
            lambda line: line.product_tmpl_id == self.product_template_b
            and line.applied_on == "product_template"
        )
        self.assertTrue(cond_line)
        self.assertEqual(cond_line.seller_discount, 8.0)

    def test_wizard_saves_product_line_as_variant(self):
        """Wizard saves a product line as variant-level condition."""
        order = self._create_order()
        self._create_order_line(order, product=self.product_b, seller_discount=8.0)
        result = order.action_open_save_condition_wizard()
        wizard = self.env["tr.save.condition.wizard"].browse(result["res_id"])
        wizard.line_ids.save_as = "variant"
        wizard.action_save()
        cond_line = self.condition.line_ids.filtered(
            lambda line: line.product_id == self.product_b
        )
        self.assertTrue(cond_line)
        self.assertEqual(cond_line.seller_discount, 8.0)

    def test_wizard_ignores_line(self):
        """Wizard ignores lines marked as ignore."""
        order = self._create_order()
        self._create_order_line(order, product=self.product_b, seller_discount=8.0)
        result = order.action_open_save_condition_wizard()
        wizard = self.env["tr.save.condition.wizard"].browse(result["res_id"])
        wizard.line_ids.ignore = True
        wizard.action_save()
        cond_line = self.condition.line_ids.filtered(
            lambda line: line.product_tmpl_id == self.product_template_b
        )
        self.assertFalse(cond_line)

    def test_wizard_creates_condition_if_missing(self):
        """Wizard creates a new condition if the partner has none."""
        self.customer.commercial_condition_id = False
        # Re-browse in admin env (self.condition carries salesperson env from setup)
        self.env["partner.commercial.condition"].browse(self.condition.id).unlink()
        order = self._create_order()
        order.cash_discount = 3.0
        result = order.action_open_save_condition_wizard()
        wizard = self.env["tr.save.condition.wizard"].browse(result["res_id"])
        wizard.update_cash_discount = True
        # Admin is in group_system which implies group_sales_director,
        # so _check_user_profile is bypassed for the default test user.
        wizard.action_save()
        new_condition = self.customer.commercial_condition_id
        self.assertTrue(new_condition)
        self.assertEqual(new_condition.cash_discount, 3.0)

    def test_wizard_updates_existing_condition_line(self):
        """Wizard updates an existing condition line instead of creating duplicate."""
        # Create an existing template line
        self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "product_tmpl_id": self.product_template_b.id,
                "seller_discount": 3.0,
            }
        )
        order = self._create_order()
        self._create_order_line(order, product=self.product_b, seller_discount=8.0)
        result = order.action_open_save_condition_wizard()
        wizard = self.env["tr.save.condition.wizard"].browse(result["res_id"])
        wizard.line_ids.save_as = "template"
        wizard.action_save()
        cond_lines = self.condition.line_ids.filtered(
            lambda line: line.product_tmpl_id == self.product_template_b
            and line.applied_on == "product_template"
        )
        self.assertEqual(len(cond_lines), 1)
        self.assertEqual(cond_lines.seller_discount, 8.0)

    def test_wizard_all_same_discount(self):
        """all_same_discount is True when all non-ignored lines have same discount."""
        order = self._create_order()
        self._create_order_line(order, seller_discount=8.0)
        self._create_order_line(order, product=self.product_b, seller_discount=8.0)
        result = order.action_open_save_condition_wizard()
        wizard = self.env["tr.save.condition.wizard"].browse(result["res_id"])
        self.assertTrue(wizard.all_same_discount)

    def test_wizard_updates_seller_discount(self):
        """Wizard with update_seller_discount=True saves seller_discount."""
        order = self._create_order()
        self._create_order_line(order, seller_discount=7.0)
        result = order.action_open_save_condition_wizard()
        wizard = self.env["tr.save.condition.wizard"].browse(result["res_id"])
        wizard.update_seller_discount = True
        wizard.seller_discount_new = 7.0
        wizard.action_save()
        self.assertAlmostEqual(self.condition.seller_discount, 7.0, places=2)


@tagged("post_install", "-at_install")
class TestSaveConditionLineWizard(CommercialPolicyTestCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

    def test_line_wizard_saves_as_template(self):
        """Per-line wizard saves discount at template level."""
        order = self._create_order()
        line = self._create_order_line(
            order, product=self.product_b, seller_discount=8.0
        )
        result = line.action_open_save_condition_line_wizard()
        wizard = self.env["tr.save.condition.line.wizard"].browse(result["res_id"])
        wizard.save_as = "template"
        wizard.action_save()
        cond_line = self.condition.line_ids.filtered(
            lambda cline: cline.product_tmpl_id == self.product_template_b
            and cline.applied_on == "product_template"
        )
        self.assertTrue(cond_line)
        self.assertEqual(cond_line.seller_discount, 8.0)

    def test_line_wizard_saves_as_variant(self):
        """Per-line wizard saves discount at variant level."""
        order = self._create_order()
        line = self._create_order_line(
            order, product=self.product_b, seller_discount=8.0
        )
        result = line.action_open_save_condition_line_wizard()
        wizard = self.env["tr.save.condition.line.wizard"].browse(result["res_id"])
        wizard.save_as = "variant"
        wizard.action_save()
        cond_line = self.condition.line_ids.filtered(
            lambda cline: cline.product_id == self.product_b
        )
        self.assertTrue(cond_line)
        self.assertEqual(cond_line.seller_discount, 8.0)

    def test_line_wizard_do_not_save(self):
        """Per-line wizard with save_as='none' does not save anything."""
        order = self._create_order()
        line = self._create_order_line(
            order, product=self.product_b, seller_discount=8.0
        )
        result = line.action_open_save_condition_line_wizard()
        wizard = self.env["tr.save.condition.line.wizard"].browse(result["res_id"])
        wizard.save_as = "none"
        wizard.action_save()
        cond_line = self.condition.line_ids.filtered(
            lambda cline: cline.product_tmpl_id == self.product_template_b
        )
        self.assertFalse(cond_line)

    def test_line_wizard_source_level_general(self):
        """Per-line wizard detects source_level as 'general' correctly."""
        order = self._create_order()
        line = self._create_order_line(
            order, product=self.product_b, seller_discount=8.0
        )
        result = line.action_open_save_condition_line_wizard()
        wizard = self.env["tr.save.condition.line.wizard"].browse(result["res_id"])
        self.assertEqual(wizard.source_level, "general")

    def test_line_wizard_source_level_variant(self):
        """Per-line wizard detects source_level as 'variant' correctly."""
        # Create a variant-level condition line
        self.env["partner.commercial.condition.line"].create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product",
                "product_id": self.product_b.id,
                "seller_discount": 6.0,
            }
        )
        order = self._create_order()
        line = self._create_order_line(
            order, product=self.product_b, seller_discount=8.0
        )
        result = line.action_open_save_condition_line_wizard()
        wizard = self.env["tr.save.condition.line.wizard"].browse(result["res_id"])
        self.assertEqual(wizard.source_level, "variant")
        self.assertEqual(wizard.save_as, "variant")

    def test_line_wizard_creates_condition_if_missing(self):
        """Per-line wizard creates condition if partner has none."""
        self.customer.commercial_condition_id = False
        # Re-browse in admin env (self.condition carries salesperson env from setup)
        self.env["partner.commercial.condition"].browse(self.condition.id).unlink()
        order = self._create_order()
        line = self._create_order_line(
            order, product=self.product_b, seller_discount=0.0
        )
        result = line.action_open_save_condition_line_wizard()
        wizard = self.env["tr.save.condition.line.wizard"].browse(result["res_id"])
        wizard.save_as = "template"
        wizard.action_save()
        new_condition = self.customer.commercial_condition_id
        self.assertTrue(new_condition)
        cond_line = new_condition.line_ids.filtered(
            lambda cline: cline.product_tmpl_id == self.product_template_b
        )
        self.assertTrue(cond_line)

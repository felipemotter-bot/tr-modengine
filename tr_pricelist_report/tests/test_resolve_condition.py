# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError

from .common import PricelistReportTestCommon


class TestResolveCondition(PricelistReportTestCommon):
    def test_condition_button_opens_wizard(self):
        """Button on the condition form opens the wizard pre-filled."""
        action = self.condition.action_print_pricelist()
        self.assertEqual(action["res_model"], "tr.pricelist.report.wizard")
        self.assertEqual(action["context"]["default_condition_id"], self.condition.id)

    def test_partner_button_uses_effective_condition(self):
        """Partner button resolves the effective condition and opens wizard."""
        action = self.customer.action_print_pricelist()
        self.assertEqual(action["res_model"], "tr.pricelist.report.wizard")
        self.assertEqual(
            action["context"]["default_condition_id"],
            self.customer.effective_condition_id.id,
        )

    def test_partner_button_blocks_when_no_condition(self):
        """Partner button raises when no effective condition exists."""
        other_partner = self.env["res.partner"].create({"name": "No Condition"})
        with self.assertRaises(UserError):
            other_partner.action_print_pricelist()

    def test_default_condition_from_partner_context(self):
        """Wizard defaults ``condition_id`` from partner ``active_id``."""
        wizard = (
            self.env["tr.pricelist.report.wizard"]
            .with_context(active_model="res.partner", active_id=self.customer.id)
            .create({"category_ids": [(6, 0, [self.categ_chemicals.id])]})
        )
        self.assertEqual(wizard.condition_id, self.condition)

    def test_default_condition_without_context(self):
        """No active context → no default condition."""
        wizard_model = self.env["tr.pricelist.report.wizard"]
        self.assertFalse(wizard_model._default_condition_id())
        self.assertEqual(wizard_model._default_discount_display(), "net_price")

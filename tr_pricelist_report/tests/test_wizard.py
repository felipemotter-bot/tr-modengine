# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import PricelistReportTestCommon


class TestWizard(PricelistReportTestCommon):
    def test_default_discount_display_from_condition(self):
        """``discount_display`` defaults to condition's value."""
        self.condition.discount_display = "show_discounts"
        wizard = (
            self.env["tr.pricelist.report.wizard"]
            .with_context(
                active_model="partner.commercial.condition",
                active_id=self.condition.id,
            )
            .create({"category_ids": [(6, 0, [self.categ_chemicals.id])]})
        )
        self.assertEqual(wizard.discount_display, "show_discounts")

    def test_action_generate_returns_report(self):
        """``action_generate`` returns the report action."""
        wizard = self._open_wizard(category_ids=[self.categ_chemicals.id])
        action = wizard.action_generate()
        self.assertEqual(action["type"], "ir.actions.report")

    def test_action_generate_accepts_empty_category_ids(self):
        """After PR-C, ``geral`` prints the whole sellable list when
        ``category_ids`` is empty. No more "pick at least one" block.
        """
        wizard = self.env["tr.pricelist.report.wizard"].create(
            {"condition_id": self.condition.id}
        )
        action = wizard.action_generate()
        self.assertEqual(action["type"], "ir.actions.report")

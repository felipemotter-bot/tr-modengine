# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from pathlib import Path

from lxml import etree

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDiscountCalculatorWidgetTemplate(TransactionCase):
    """Guards the OWL template structure for ``order_discount_calculator``.

    The cash_discount field uses this widget on the sale order form.
    Without the ``t-if="!props.readonly"`` swap, the input keeps the
    ``o_input`` border-bottom styling on confirmed orders and looks
    editable even when the policy locks edits to draft/sent.
    """

    TEMPLATE_PATH = (
        Path(__file__).resolve().parent.parent
        / "static"
        / "src"
        / "components"
        / "order_discount_calculator"
        / "order_discount_calculator.xml"
    )

    def _widget_template(self):
        tree = etree.parse(str(self.TEMPLATE_PATH))
        templates = tree.xpath(
            "//t[@t-name='tr_commercial_policy.OrderDiscountCalculator']"
        )
        self.assertEqual(
            len(templates),
            1,
            "Widget template must be defined exactly once.",
        )
        return templates[0]

    def test_input_renders_only_when_editable(self):
        node = self._widget_template().xpath(".//input")
        self.assertEqual(
            len(node),
            1,
            "Widget template should expose a single input node.",
        )
        self.assertEqual(node[0].attrib.get("t-if"), "!props.readonly")

    def test_span_fallback_present_for_readonly(self):
        """A ``<span t-else>`` keeps the value visible without underline."""
        node = self._widget_template().xpath(".//span[@t-else]")
        self.assertEqual(
            len(node),
            1,
            "Widget template should render a span fallback when readonly.",
        )
        self.assertEqual(node[0].attrib.get("t-esc"), "formattedValue")

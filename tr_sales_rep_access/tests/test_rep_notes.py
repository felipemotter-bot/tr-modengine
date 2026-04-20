# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from lxml import etree

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import SalesRepAccessTestCommon


@tagged("post_install", "-at_install")
class TestRepNotes(SalesRepAccessTestCommon):
    """Coverage for the PR 6a ``tr_rep_notes`` field on ``sale.order``.

    Free-text operational notes on the order. Rep edits while the
    quotation is in draft (the PR 1 guard ``_sales_rep_check_rep_can_
    edit`` blocks rep writes on confirmed orders, including this
    field — intentional, Felipe 2026-04-19). Manager/admin keeps
    control post-confirm. No tracking, no group restriction on the
    field itself.
    """

    def test_rep_can_write_tr_rep_notes_on_draft(self):
        order = self._make_order(self.customer_c1)
        order.with_user(self.user_u1).write(
            {"tr_rep_notes": "Cliente pediu 10% extra por volume."}
        )
        self.assertEqual(
            order.tr_rep_notes,
            "Cliente pediu 10% extra por volume.",
        )

    def test_rep_cannot_write_tr_rep_notes_on_confirmed(self):
        # Regression guarding Felipe's decision (2026-04-19): the
        # PR 1 ``_sales_rep_check_rep_can_edit`` guard still applies
        # to ``tr_rep_notes``. No whitelist exception — rep's notes
        # are a draft-phase scratchpad; after confirm the manager
        # holds the pen.
        order = self._make_order(self.customer_c1)
        order.action_confirm()
        with self.assertRaises(AccessError):
            order.with_user(self.user_u1).write(
                {"tr_rep_notes": "Tentativa pós-confirm."}
            )

    def test_admin_can_write_tr_rep_notes_on_confirmed(self):
        # Manager/admin keeps editing the field after confirm. The
        # PR 1 guard is rep-specific (checks ``has_group`` of the
        # rep group), so admin falls through unaffected.
        order = self._make_order(self.customer_c1)
        order.action_confirm()
        admin = self.env.ref("base.user_admin")
        order.with_user(admin).write(
            {"tr_rep_notes": "Ajustar frete pra CIF antes de enviar."}
        )
        self.assertEqual(
            order.tr_rep_notes,
            "Ajustar frete pra CIF antes de enviar.",
        )

    def test_tr_rep_notes_is_not_tracked(self):
        # Guard against a future contributor flipping ``tracking=True``
        # without thinking about chatter noise — this is an
        # operational field, not audit trail.
        field = self.env["sale.order"]._fields["tr_rep_notes"]
        self.assertFalse(
            getattr(field, "tracking", False),
            "tr_rep_notes must stay untracked — it's operational notes, "
            "not audit trail; if you need history, open a dedicated PR.",
        )

    def test_tr_rep_notes_appears_in_other_information_page(self):
        # View regression: the field must show up under the
        # ``other_information`` page so admins, reps and managers
        # find it in the same place regardless of who opens the form.
        view = self.env["sale.order"].fields_view_get(view_type="form")
        tree = etree.fromstring(view["arch"])
        nodes = tree.xpath(
            "//page[@name='other_information']"
            "//group[@name='sales_rep']/field[@name='tr_rep_notes']"
        )
        self.assertTrue(
            nodes,
            "tr_rep_notes must live inside the Sales Rep group on the "
            "Other Info page",
        )

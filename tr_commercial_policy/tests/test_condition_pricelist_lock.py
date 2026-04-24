# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestConditionPricelistLock(CommercialPolicyTestCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.pricelist_alt = cls.env["product.pricelist"].create(
            {
                "name": "Alt Pricelist",
                "currency_id": cls.env.ref("base.BRL").id,
            }
        )
        cls.agent_profile.pricelist_ids = [(4, cls.pricelist_alt.id)]
        cls.salesperson.partner_id.sales_profile_id = cls.agent_profile
        cls.env.company.default_sales_profile_id = cls.agent_profile

        # product.pricelist._order = "sequence asc, id desc", so the
        # "first" pricelist in pricelist_ids (== the profile default
        # via _resolve_default_pricelist_for_partner) is the most
        # recently created one. Resolve it dynamically to make the
        # tests order-independent.
        Condition = cls.env["partner.commercial.condition"]
        cls.default_pl = Condition._resolve_default_pricelist_for_partner(cls.customer)
        cls.other_pl = (cls.pricelist | cls.pricelist_alt) - cls.default_pl

        cls.condition = Condition.create(
            {
                "partner_id": cls.customer.id,
                "pricelist_id": cls.default_pl.id,
            }
        )

    def test_manager_can_change_pricelist(self):
        self.condition.with_user(self.manager_user).write(
            {"pricelist_id": self.other_pl.id}
        )
        self.assertEqual(self.condition.pricelist_id, self.other_pl)

    def test_salesperson_cannot_change_pricelist(self):
        with self.assertRaises(AccessError):
            self.condition.with_user(self.salesperson).write(
                {"pricelist_id": self.other_pl.id}
            )

    def test_sudo_can_change_pricelist(self):
        self.condition.with_user(self.salesperson).sudo().write(
            {"pricelist_id": self.other_pl.id}
        )
        self.assertEqual(self.condition.pricelist_id, self.other_pl)

    def test_write_without_pricelist_not_blocked(self):
        self.condition.with_user(self.salesperson).write({"cash_discount": 1.0})
        self.assertEqual(self.condition.cash_discount, 1.0)

    def test_non_manager_create_without_explicit_pricelist(self):
        other_customer = self.env["res.partner"].create({"name": "Other Customer"})
        Condition = self.env["partner.commercial.condition"]
        expected = Condition._resolve_default_pricelist_for_partner(other_customer)
        condition = Condition.with_user(self.salesperson).create(
            {"partner_id": other_customer.id}
        )
        self.assertEqual(condition.pricelist_id, expected)

    def test_non_manager_create_with_default_pricelist(self):
        other_customer = self.env["res.partner"].create({"name": "Other Customer 2"})
        Condition = self.env["partner.commercial.condition"]
        expected = Condition._resolve_default_pricelist_for_partner(other_customer)
        condition = Condition.with_user(self.salesperson).create(
            {
                "partner_id": other_customer.id,
                "pricelist_id": expected.id,
            }
        )
        self.assertEqual(condition.pricelist_id, expected)

    def test_non_manager_create_with_override_pricelist_blocked(self):
        other_customer = self.env["res.partner"].create({"name": "Other Customer 3"})
        Condition = self.env["partner.commercial.condition"]
        expected = Condition._resolve_default_pricelist_for_partner(other_customer)
        non_default = (self.pricelist | self.pricelist_alt) - expected
        with self.assertRaises(AccessError):
            Condition.with_user(self.salesperson).create(
                {
                    "partner_id": other_customer.id,
                    "pricelist_id": non_default.id,
                }
            )

    def test_sudo_create_with_override_pricelist_ok(self):
        other_customer = self.env["res.partner"].create({"name": "Other Customer 4"})
        Condition = self.env["partner.commercial.condition"]
        expected = Condition._resolve_default_pricelist_for_partner(other_customer)
        non_default = (self.pricelist | self.pricelist_alt) - expected
        condition = (
            Condition.with_user(self.salesperson)
            .sudo()
            .create(
                {
                    "partner_id": other_customer.id,
                    "pricelist_id": non_default.id,
                }
            )
        )
        self.assertEqual(condition.pricelist_id, non_default)

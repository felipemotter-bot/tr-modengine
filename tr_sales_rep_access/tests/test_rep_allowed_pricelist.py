# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import SalesRepAccessTestCommon


class TestRepAllowedPricelist(SalesRepAccessTestCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pricelist_extra_a = cls.env["product.pricelist"].create(
            {
                "name": "Rep Allowed Extra A",
                "currency_id": cls.env.ref("base.BRL").id,
            }
        )
        cls.pricelist_extra_b = cls.env["product.pricelist"].create(
            {
                "name": "Rep Allowed Extra B",
                "currency_id": cls.env.ref("base.BRL").id,
            }
        )

    def test_empty_allowed_list_sees_all(self):
        """With empty allowed_pricelist_ids the rep sees every pricelist."""
        self.assertFalse(self.agent_a1.allowed_pricelist_ids)
        ids = self.env["product.pricelist"].with_user(self.user_u1).search([]).ids
        self.assertIn(self.pricelist.id, ids)
        self.assertIn(self.pricelist_extra_a.id, ids)
        self.assertIn(self.pricelist_extra_b.id, ids)

    def test_non_empty_allowed_list_narrows_search(self):
        """Filled allowed_pricelist_ids restricts what the rep can see."""
        self.agent_a1.allowed_pricelist_ids = [
            (6, 0, [self.pricelist_extra_a.id]),
        ]
        ids = self.env["product.pricelist"].with_user(self.user_u1).search([]).ids
        self.assertEqual(ids, [self.pricelist_extra_a.id])

    def test_rep_cannot_read_pricelist_outside_allowed(self):
        """Pricelists outside the allowed list are invisible on read."""
        self.agent_a1.allowed_pricelist_ids = [
            (6, 0, [self.pricelist_extra_a.id]),
        ]
        visible = (
            self.env["product.pricelist"]
            .with_user(self.user_u1)
            .search([("id", "=", self.pricelist_extra_b.id)])
        )
        self.assertFalse(
            visible,
            "Pricelist outside allowed list must be filtered out by ir.rule.",
        )

    def test_admin_sees_all_regardless_of_rep_config(self):
        """The rule is conditional on the rep group; admin is unaffected."""
        self.agent_a1.allowed_pricelist_ids = [
            (6, 0, [self.pricelist_extra_a.id]),
        ]
        ids = self.env["product.pricelist"].search([]).ids
        self.assertIn(self.pricelist.id, ids)
        self.assertIn(self.pricelist_extra_a.id, ids)
        self.assertIn(self.pricelist_extra_b.id, ids)

    def test_other_rep_unaffected_by_another_reps_list(self):
        """Each rep is scoped by its own partner's allowed list."""
        self.agent_a1.allowed_pricelist_ids = [
            (6, 0, [self.pricelist_extra_a.id]),
        ]
        # A2 has no allowed list — still sees everything.
        ids = self.env["product.pricelist"].with_user(self.user_u2).search([]).ids
        self.assertIn(self.pricelist.id, ids)
        self.assertIn(self.pricelist_extra_a.id, ids)
        self.assertIn(self.pricelist_extra_b.id, ids)

    def test_field_hidden_from_rep_fields_get(self):
        """Field declares groups=manager; rep cannot see it via fields_get."""
        fields_as_rep = self.env["res.partner"].with_user(self.user_u1).fields_get()
        self.assertNotIn("allowed_pricelist_ids", fields_as_rep)

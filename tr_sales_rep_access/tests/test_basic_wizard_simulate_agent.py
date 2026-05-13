# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from lxml import etree

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import SalesRepAccessTestCommon


@tagged("post_install", "-at_install")
class TestBasicWizardSimulateAgent(SalesRepAccessTestCommon):
    """Manager-only ``simulate_as_agent_id`` field on the Basic Pricelist.

    Exercises the override of ``tr.pricelist.basic.wizard._resolve_products``
    declared in ``tr_sales_rep_access`` plus its companion view inherit.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager_group = cls.env.ref("sales_team.group_sale_manager")
        cls.user_manager = cls.env["res.users"].create(
            {
                "name": "Sales Manager",
                "login": "tsra_manager_basic_sim",
                "groups_id": [(6, 0, [cls.manager_group.id])],
            }
        )
        # Two extra products in non-overlapping branches to exercise the
        # filter clearly. ``common.SalesRepAccessTestCommon`` already
        # creates products in ``cat_allowed``, ``cat_allowed_sub``,
        # ``cat_excluded_sub`` and ``cat_other``; A1's catalog is
        # allowed=cat_allowed, excluded=cat_excluded_sub.

    def _new_wizard(self, user, **vals):
        Wizard = self.env["tr.pricelist.basic.wizard"].with_user(user)
        defaults = {
            "company_id": self.company.id,
            "pricelist_id": self.pricelist.id,
        }
        defaults.update(vals)
        return Wizard.create(defaults)

    def test_manager_with_agent_filters_to_visible_categories(self):
        """Simulating as A1 must return only products in cat_allowed
        minus descendants of cat_excluded_sub."""
        wizard = self._new_wizard(
            self.user_manager, simulate_as_agent_id=self.agent_a1.id
        )
        products = wizard._resolve_products(company_id=self.company.id)
        categ_ids = set(products.mapped("categ_id.id"))
        self.assertIn(self.cat_allowed.id, categ_ids)
        self.assertIn(self.cat_allowed_sub.id, categ_ids)
        self.assertNotIn(
            self.cat_excluded_sub.id,
            categ_ids,
            "Excluded subtree must not leak into simulated view.",
        )
        self.assertNotIn(
            self.cat_other.id,
            categ_ids,
            "Categories outside cat_allowed must not appear.",
        )

    def test_manager_without_agent_returns_full_catalog(self):
        """No simulation set → behavior matches the upstream resolver."""
        wizard_sim = self._new_wizard(self.user_manager)
        wizard_base = self.env["tr.pricelist.basic.wizard"].create(
            {
                "company_id": self.company.id,
                "pricelist_id": self.pricelist.id,
            }
        )
        sim_ids = set(wizard_sim._resolve_products(company_id=self.company.id).ids)
        base_ids = set(wizard_base._resolve_products(company_id=self.company.id).ids)
        self.assertEqual(sim_ids, base_ids)

    def test_manager_with_agent_empty_allowed_raises_user_error(self):
        """A2 has no allowed_category_ids → fail-safe: no products → wizard raises."""
        wizard = self._new_wizard(
            self.user_manager, simulate_as_agent_id=self.agent_a2.id
        )
        with self.assertRaises(UserError):
            wizard._get_report_values([wizard.id])

    def test_rep_external_can_generate_basic_wizard(self):
        """Regression: rep external still creates and resolves the
        wizard without hitting ``AccessError`` from the new field."""
        wizard = (
            self.env["tr.pricelist.basic.wizard"]
            .with_user(self.user_u1)
            .create(
                {
                    "company_id": self.company.id,
                    "pricelist_id": self.pricelist.id,
                }
            )
        )
        # End-to-end resolution must succeed and stay constrained to
        # A1's view via the ``product.product._search`` override. The
        # new field is invisible to the rep, so the simulation path is
        # not taken — but ``_get_report_values`` must not raise and the
        # resolved product set must already exclude forbidden branches.
        products = wizard._resolve_products(company_id=self.company.id)
        categ_ids = set(products.mapped("categ_id.id"))
        self.assertNotIn(self.cat_other.id, categ_ids)
        self.assertNotIn(self.cat_excluded_sub.id, categ_ids)
        values = wizard._get_report_values([wizard.id])
        self.assertTrue(values["sections"])

    def test_rep_external_fields_get_hides_simulate_field(self):
        """The field must not appear in fields_get for a rep user."""
        fields_info = (
            self.env["tr.pricelist.basic.wizard"].with_user(self.user_u1).fields_get()
        )
        self.assertNotIn("simulate_as_agent_id", fields_info)

    def test_rep_external_get_view_omits_simulate_field(self):
        """The view arch served to a rep must not contain the field node."""
        view = (
            self.env["tr.pricelist.basic.wizard"]
            .with_user(self.user_u1)
            .get_view(view_type="form")
        )
        arch = etree.fromstring(view["arch"])
        self.assertFalse(
            arch.xpath("//field[@name='simulate_as_agent_id']"),
            "simulate_as_agent_id node must be stripped from the rep view.",
        )

    def test_manager_get_view_contains_simulate_field(self):
        """Sanity: the field is present for a manager."""
        view = (
            self.env["tr.pricelist.basic.wizard"]
            .with_user(self.user_manager)
            .get_view(view_type="form")
        )
        arch = etree.fromstring(view["arch"])
        self.assertTrue(
            arch.xpath("//field[@name='simulate_as_agent_id']"),
            "simulate_as_agent_id must be visible to managers.",
        )

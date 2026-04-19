# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError

from .common import SalesRepAccessTestCommon


class TestCatalogSecurity(SalesRepAccessTestCommon):
    """Server-side protection of catalog fields against RPC bypass.

    ``allowed_category_ids`` and ``excluded_category_ids`` are
    declared with field-level ``groups="tr_commercial_policy.
    group_sales_manager"``. Users without that group (including
    reps) must not be able to read or write those fields, no matter
    the client surface (RPC, ORM, search).
    """

    def test_rep_cannot_read_allowed_category_ids(self):
        """Field-level groups hides the column from read/fields_get."""
        partner = self.agent_a1.with_user(self.user_u1)
        self.assertNotIn("allowed_category_ids", partner.fields_get())
        self.assertNotIn("excluded_category_ids", partner.fields_get())

    def test_rep_cannot_write_allowed_category_ids(self):
        """RPC write attempt on rep's own partner raises AccessError."""
        partner = self.agent_a1.with_user(self.user_u1)
        with self.assertRaises(AccessError):
            partner.write({"allowed_category_ids": [(6, 0, [self.cat_other.id])]})

    def test_rep_cannot_write_excluded_category_ids(self):
        """Same guarantee for excluded_category_ids."""
        partner = self.agent_a1.with_user(self.user_u1)
        with self.assertRaises(AccessError):
            partner.write({"excluded_category_ids": [(6, 0, [])]})

    def test_manager_can_read_catalog_fields(self):
        """Sales manager (admin by default) still sees the fields."""
        fields = self.agent_a1.fields_get()
        self.assertIn("allowed_category_ids", fields)
        self.assertIn("excluded_category_ids", fields)

    def test_plain_sales_manager_can_read_catalog_fields(self):
        """A user with only Sales Manager group sees the fields.

        Regression guard: ``admin`` reaches the fields through the
        implies chain, but a real operator running as ``group_sales_-
        manager`` (no Sales Team / Commission / Admin groups) must
        also see them — otherwise the PR breaks the very user it
        was designed to serve.
        """
        manager = self.env["res.users"].create(
            {
                "name": "Plain Sales Manager",
                "login": "tsra_plain_manager",
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            self.env.ref("tr_commercial_policy.group_sales_manager").id,
                        ],
                    ),
                ],
            }
        )
        fields = self.agent_a1.with_user(manager).fields_get()
        self.assertIn("allowed_category_ids", fields)
        self.assertIn("excluded_category_ids", fields)

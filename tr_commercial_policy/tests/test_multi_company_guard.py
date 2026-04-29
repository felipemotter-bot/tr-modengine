# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError

from .common import CommercialPolicyTestCommon


class TestMultiCompanyGuard(CommercialPolicyTestCommon):
    """Cover the ``check_company`` + ``_check_company_auto`` protection on
    ``partner.commercial.condition``.

    Regression: Felipe hit an AccessError printing a pricelist because the
    condition of a TRENTO partner had ``payment_mode_id`` pointing to a
    record of another company. Without check_company, nothing prevented
    that cross-company assignment. These tests lock the guard for every
    ``check_company=True`` Many2one on the model.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Two companies — the condition lives in company_a; company_b's
        # records are the "forbidden" ones that can't be assigned.
        cls.company_a = cls.env.ref("base.main_company")
        cls.company_b = cls.env["res.company"].create(
            {"name": "Multi-Company Guard Test B"}
        )
        cls.env.user.company_ids = [(4, cls.company_b.id)]

        # Pricelists
        cls.pricelist_a = cls.env["product.pricelist"].create(
            {"name": "PL A", "company_id": cls.company_a.id}
        )
        cls.pricelist_b = cls.env["product.pricelist"].create(
            {"name": "PL B", "company_id": cls.company_b.id}
        )
        cls.pricelist_shared = cls.env["product.pricelist"].create(
            {"name": "PL Shared", "company_id": False}
        )

        # Payment terms
        cls.payment_term_a = cls.env["account.payment.term"].create(
            {
                "name": "PT A",
                "company_id": cls.company_a.id,
                "line_ids": [(0, 0, {"value": "balance", "days": 0})],
            }
        )
        cls.payment_term_b = cls.env["account.payment.term"].create(
            {
                "name": "PT B",
                "company_id": cls.company_b.id,
                "line_ids": [(0, 0, {"value": "balance", "days": 0})],
            }
        )

        # Payment modes (account_payment_mode)
        cls.payment_mode_a = cls._create_payment_mode(cls.company_a)
        cls.payment_mode_b = cls._create_payment_mode(cls.company_b)

        # Delivery carriers. delivery.carrier needs a service product for
        # rate computation, and its _check_company cross-checks it against
        # the carrier's own ``company_id``. Create a company-scoped service
        # product for each carrier so the fixture itself stays consistent.
        carrier_product_a = cls.env["product.product"].create(
            {
                "name": "Carrier Product A",
                "type": "service",
                "company_id": cls.company_a.id,
            }
        )
        carrier_product_b = cls.env["product.product"].create(
            {
                "name": "Carrier Product B",
                "type": "service",
                "company_id": cls.company_b.id,
            }
        )
        cls.carrier_a = cls.env["delivery.carrier"].create(
            {
                "name": "Carrier A",
                "company_id": cls.company_a.id,
                "product_id": carrier_product_a.id,
            }
        )
        cls.carrier_b = (
            cls.env["delivery.carrier"]
            .with_company(cls.company_b)
            .create(
                {
                    "name": "Carrier B",
                    "company_id": cls.company_b.id,
                    "product_id": carrier_product_b.id,
                }
            )
        )

    @classmethod
    def _create_payment_mode(cls, company):
        """Helper: build a payment_mode tied to ``company``.

        account.payment.mode requires a fixed_journal_id and a
        payment_method_id. We reuse the first matching ones from the
        company, avoiding assumptions about chart-of-account fixtures.
        """
        journal = cls.env["account.journal"].search(
            [("company_id", "=", company.id), ("type", "in", ("bank", "cash"))],
            limit=1,
        )
        if not journal:
            journal = cls.env["account.journal"].create(
                {
                    "name": "Cash Guard %s" % company.id,
                    "type": "cash",
                    "code": "CHG%s" % company.id,
                    "company_id": company.id,
                }
            )
        payment_method = cls.env.ref("account.account_payment_method_manual_in")
        return cls.env["account.payment.mode"].create(
            {
                "name": "PM Guard %s" % company.id,
                "company_id": company.id,
                "payment_method_id": payment_method.id,
                "bank_account_link": "fixed",
                "fixed_journal_id": journal.id,
            }
        )

    def _make_condition(self, **overrides):
        """Factory of a minimal valid condition in company_a.

        Uses a fresh partner each call to avoid colliding with the
        (partner_id, company_id) unique constraint — the test common
        setUp already creates a condition for ``self.customer``.
        """
        partner = self.env["res.partner"].create(
            {"name": "Multi-Company Guard Partner"}
        )
        vals = {
            "partner_id": partner.id,
            "company_id": self.company_a.id,
            "pricelist_id": self.pricelist_a.id,
        }
        vals.update(overrides)
        return self.env["partner.commercial.condition"].create(vals)

    # ------------------------------------------------------------------
    # check_company on each of the four multi-company fields
    # ------------------------------------------------------------------

    def test_pricelist_from_wrong_company_blocked_on_create(self):
        with self.assertRaises(UserError):
            self.env["partner.commercial.condition"].create(
                {
                    "partner_id": self.customer.id,
                    "company_id": self.company_a.id,
                    "pricelist_id": self.pricelist_b.id,
                }
            )

    def test_payment_term_from_wrong_company_blocked_on_write(self):
        condition = self._make_condition()
        with self.assertRaises(UserError):
            condition.payment_term_id = self.payment_term_b

    def test_payment_mode_from_wrong_company_blocked_on_write(self):
        condition = self._make_condition()
        with self.assertRaises(UserError):
            condition.payment_mode_id = self.payment_mode_b

    def test_delivery_carrier_from_wrong_company_blocked_on_write(self):
        condition = self._make_condition()
        with self.assertRaises(UserError):
            condition.delivery_carrier_id = self.carrier_b

    # ------------------------------------------------------------------
    # Valid assignments: same company and company-less
    # ------------------------------------------------------------------

    def test_same_company_assignments_pass(self):
        condition = self._make_condition(
            payment_term_id=self.payment_term_a.id,
            payment_mode_id=self.payment_mode_a.id,
            delivery_carrier_id=self.carrier_a.id,
        )
        self.assertEqual(condition.payment_term_id, self.payment_term_a)
        self.assertEqual(condition.payment_mode_id, self.payment_mode_a)
        self.assertEqual(condition.delivery_carrier_id, self.carrier_a)

    def test_company_less_pricelist_is_allowed(self):
        """Company-less (shared) records pass check_company regardless."""
        condition = self._make_condition(pricelist_id=self.pricelist_shared.id)
        self.assertEqual(condition.pricelist_id, self.pricelist_shared)

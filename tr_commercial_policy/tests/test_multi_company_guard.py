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

    # ------------------------------------------------------------------
    # Hook SQL: regression on the company-scoped ``ir.property`` reads
    # and writes. The root cause of Felipe's AccessError in production
    # was ``_create_condition_sql`` reading ``ir.property`` without
    # filtering by ``company_id`` (``LIMIT 1`` picked an arbitrary
    # company's value). These tests nail down the semantic contract so
    # a future refactor of the hook SQL can't silently regress.
    # ------------------------------------------------------------------

    def _insert_ir_property(
        self, name, partner_id, target_model, target_id, company_id
    ):
        """Write an ``ir.property`` row via SQL for test setup.

        Using SQL directly here mirrors how the hook reads them, which
        is the behavior we want to exercise.
        """
        self.env.cr.execute(
            """
            SELECT id FROM ir_model_fields
            WHERE model = 'res.partner' AND name = %s
            """,
            (name,),
        )
        row = self.env.cr.fetchone()
        fields_id = row[0] if row else None
        if not fields_id:
            self.skipTest("Field ir.model.fields not present: %s" % name)
        self.env.cr.execute(
            """
            INSERT INTO ir_property
                (name, type, fields_id, company_id, res_id, value_reference)
            VALUES (%s, 'many2one', %s, %s,
                    CONCAT('res.partner,', %s::text),
                    CONCAT(%s, ',', %s::text))
            """,
            (
                name,
                fields_id,
                company_id,
                partner_id,
                target_model,
                target_id,
            ),
        )

    def test_hook_create_condition_sql_picks_property_of_target_company(self):
        """Hook scopes ``ir.property`` reads by ``target_company_id``.

        Partner has ``property_product_pricelist`` set to pricelist_a in
        company_a AND to pricelist_b in company_b. Calling the hook with
        ``target_company_id=company_a`` must pick pricelist_a, not b.
        """
        from ..hooks import _create_condition_sql

        partner = self.env["res.partner"].create({"name": "Hook Scope Test"})
        self._insert_ir_property(
            "property_product_pricelist",
            partner.id,
            "product.pricelist",
            self.pricelist_a.id,
            self.company_a.id,
        )
        self._insert_ir_property(
            "property_product_pricelist",
            partner.id,
            "product.pricelist",
            self.pricelist_b.id,
            self.company_b.id,
        )

        condition_id = _create_condition_sql(
            self.env.cr,
            partner.id,
            default_pricelist_id=self.pricelist_shared.id,
            target_company_id=self.company_a.id,
        )
        condition = self.env["partner.commercial.condition"].browse(condition_id)
        self.assertEqual(condition.pricelist_id, self.pricelist_a)
        self.assertEqual(condition.company_id, self.company_a)

    def test_hook_create_condition_sql_falls_back_to_company_less_property(self):
        """No company-scoped ``ir.property`` → falls back to company-less one.

        ``ORDER BY company_id NULLS LAST`` lets the global value serve as
        default when the target company doesn't have its own property row.
        """
        from ..hooks import _create_condition_sql

        partner = self.env["res.partner"].create({"name": "Hook Fallback Test"})
        # Only a company-less property exists: the hook must use it.
        self._insert_ir_property(
            "property_product_pricelist",
            partner.id,
            "product.pricelist",
            self.pricelist_shared.id,
            None,
        )

        condition_id = _create_condition_sql(
            self.env.cr,
            partner.id,
            default_pricelist_id=self.pricelist_a.id,
            target_company_id=self.company_a.id,
        )
        condition = self.env["partner.commercial.condition"].browse(condition_id)
        self.assertEqual(condition.pricelist_id, self.pricelist_shared)

    def test_hook_set_condition_property_writes_target_company_id(self):
        """``_set_condition_property`` tags the ``ir.property`` with the target.

        The migration was hardcoding ``company_id=1`` on the ``ir.property``
        it inserted for ``commercial_condition_id``. Now it must match the
        target company passed into the hook.
        """
        from ..hooks import _set_condition_property

        partner = self.env["res.partner"].create({"name": "Hook Tag Test"})
        condition = self._make_condition()
        self.env.cr.execute(
            """
            SELECT id FROM ir_model_fields
            WHERE model = 'res.partner'
              AND name = 'commercial_condition_id'
            """
        )
        field_id = self.env.cr.fetchone()[0]

        _set_condition_property(
            self.env.cr,
            partner.id,
            condition.id,
            field_id,
            target_company_id=self.company_b.id,
        )

        self.env.cr.execute(
            """
            SELECT company_id FROM ir_property
            WHERE res_id = CONCAT('res.partner,', %s::text)
              AND name = 'commercial_condition_id'
            """,
            (partner.id,),
        )
        row = self.env.cr.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], self.company_b.id)

    def _commercial_condition_field_id(self):
        self.env.cr.execute(
            """
            SELECT id FROM ir_model_fields
            WHERE model = 'res.partner'
              AND name = 'commercial_condition_id'
            """
        )
        return self.env.cr.fetchone()[0]

    def test_hook_create_group_conditions_forwards_target_company(self):
        """``_create_group_conditions`` creates one condition per group head
        and the ``ir.property`` that links the head is tagged to the target
        company.
        """
        from ..hooks import _create_group_conditions

        head = self.env["res.partner"].create({"name": "Group Head Multi"})
        member = self.env["res.partner"].create({"name": "Group Member Multi"})
        result = _create_group_conditions(
            self.env.cr,
            group_heads={head.id: [member.id]},
            default_pricelist_id=self.pricelist_a.id,
            field_id=self._commercial_condition_field_id(),
            target_company_id=self.company_a.id,
        )
        self.assertIn(head.id, result)
        condition = self.env["partner.commercial.condition"].browse(result[head.id])
        self.assertEqual(condition.partner_id, head)
        self.assertEqual(condition.company_id, self.company_a)
        self.env.cr.execute(
            """
            SELECT company_id FROM ir_property
            WHERE res_id = CONCAT('res.partner,', %s::text)
              AND name = 'commercial_condition_id'
            """,
            (head.id,),
        )
        self.assertEqual(self.env.cr.fetchone()[0], self.company_a.id)

    def test_hook_assign_group_members_writes_ir_property_per_member(self):
        """``_assign_group_members`` writes one ``ir.property`` per member,
        every row tagged to the target company.
        """
        from ..hooks import _assign_group_members

        head = self.env["res.partner"].create({"name": "Assign Head"})
        member_a = self.env["res.partner"].create({"name": "Assign Member A"})
        member_b = self.env["res.partner"].create({"name": "Assign Member B"})
        condition = self._make_condition()
        _assign_group_members(
            self.env.cr,
            group_heads={head.id: [member_a.id, member_b.id]},
            group_condition_map={head.id: condition.id},
            field_id=self._commercial_condition_field_id(),
            target_company_id=self.company_a.id,
        )
        for partner_id in (member_a.id, member_b.id):
            self.env.cr.execute(
                """
                SELECT company_id FROM ir_property
                WHERE res_id = CONCAT('res.partner,', %s::text)
                  AND name = 'commercial_condition_id'
                """,
                (partner_id,),
            )
            self.assertEqual(
                self.env.cr.fetchone()[0],
                self.company_a.id,
                "member %s ir.property should be tagged to company_a" % partner_id,
            )

    def test_hook_create_individual_conditions_scopes_by_company(self):
        """``_create_individual_conditions`` creates condition +
        ``ir.property`` per individual partner, both scoped to the target
        company.
        """
        from ..hooks import _create_individual_conditions

        partner = self.env["res.partner"].create({"name": "Individual Multi"})
        _create_individual_conditions(
            self.env.cr,
            individuals=[partner.id],
            default_pricelist_id=self.pricelist_a.id,
            field_id=self._commercial_condition_field_id(),
            target_company_id=self.company_a.id,
        )
        condition = self.env["partner.commercial.condition"].search(
            [("partner_id", "=", partner.id)], limit=1
        )
        self.assertTrue(condition)
        self.assertEqual(condition.company_id, self.company_a)
        self.env.cr.execute(
            """
            SELECT company_id FROM ir_property
            WHERE res_id = CONCAT('res.partner,', %s::text)
              AND name = 'commercial_condition_id'
            """,
            (partner.id,),
        )
        self.assertEqual(self.env.cr.fetchone()[0], self.company_a.id)

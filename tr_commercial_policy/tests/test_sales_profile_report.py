# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests import tagged

from .common import CommercialPolicyTestCommon


@tagged("post_install", "-at_install")
class TestSalesProfileReport(CommercialPolicyTestCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.report = cls.env.ref("tr_commercial_policy.action_report_sales_profile")
        # Realistic bands so the rendered tables actually have rows.
        cls.agent_general_rule = cls.agent_profile.rule_ids.filtered(
            lambda r: r.applied_on == "general"
        )[:1]
        cls.agent_general_rule.commission_band_ids.unlink()
        cls.agent_general_rule.write(
            {
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 5.0, "commission_rate": 10.0}),
                    (0, 0, {"discount_up_to": 10.0, "commission_rate": 7.0}),
                ],
            }
        )
        # Pin a payment mode on the agent profile so the "Conditions to grant"
        # block exercises both the prazo and the modes branches. Build it
        # explicitly for cls.company / inbound to avoid coupling to whatever
        # payment.mode happens to exist in the test DB.
        journal = cls.env["account.journal"].search(
            [("company_id", "=", cls.company.id), ("type", "in", ("bank", "cash"))],
            limit=1,
        )
        payment_method = cls.env["account.payment.method"].search(
            [("payment_type", "=", "inbound")], limit=1
        )
        if journal and payment_method:
            cls.payment_mode = cls.env["account.payment.mode"].create(
                {
                    "name": "Test Profile Report Mode",
                    "company_id": cls.company.id,
                    "payment_method_id": payment_method.id,
                    "bank_account_link": "variable",
                    "payment_type": "inbound",
                    "fixed_journal_id": journal.id,
                }
            )
            cls.agent_profile.payment_mode_ids = [(4, cls.payment_mode.id)]

        cls.internal_general_rule = cls.internal_profile.rule_ids.filtered(
            lambda r: r.applied_on == "general"
        )[:1]
        cls.internal_general_rule.order_value_band_ids.unlink()
        cls.internal_general_rule.write(
            {
                "order_value_band_ids": [
                    (0, 0, {"order_min_amount": 1000.0, "seller_discount_max": 5.0}),
                    (0, 0, {"order_min_amount": 5000.0, "seller_discount_max": 10.0}),
                ],
            }
        )

    def _render(self, profile):
        html, _ = self.report._render_qweb_html(self.report.report_name, profile.ids)
        return html.decode() if isinstance(html, bytes) else html

    def test_render_agent_profile(self):
        html = self._render(self.agent_profile)
        self.assertIn("PERFIL DE VENDAS", html)
        self.assertIn(self.agent_profile.name, html)
        self.assertIn("Limites de Desconto", html)
        self.assertIn("Desconto à Vista", html)
        self.assertIn("Desconto FOB", html)
        self.assertIn("Faixas de Desconto do Vendedor", html)
        # Scope label rendered for the seeded "general" rule
        self.assertIn("Geral", html)
        # Qty qualifier for a rule with qty_min == 0
        self.assertIn("Sem quantidade mínima", html)
        # Agent → commission columns
        self.assertIn("Desconto até", html)
        self.assertIn("Comissão", html)
        # Bands rendered
        self.assertTrue("5.00" in html or "5,00" in html)
        self.assertTrue("10.00" in html or "10,00" in html)

    def test_render_volume_band_qty_qualifier(self):
        """Rule with qty_min > 0 must render the 'A partir de X <UoM>'
        qualifier so the same scope (e.g. two 'Geral' rules) is no
        longer ambiguous in the PDF."""
        kg = self.env.ref("uom.product_uom_kgm")
        self.env["tr.sales.profile.rule"].create(
            {
                "profile_id": self.agent_profile.id,
                "applied_on": "general",
                "qty_min": 100.0,
                "qty_uom_id": kg.id,
                "commission_band_ids": [
                    (0, 0, {"discount_up_to": 8.0, "commission_rate": 6.0}),
                ],
            }
        )
        html = self._render(self.agent_profile)
        # UoM name casing depends on the demo data ("kg" vs "KG"); compare
        # case-insensitively so the test stays robust across DB seeds.
        self.assertIn("a partir de 100 kg", html.lower())

    def test_render_internal_profile(self):
        html = self._render(self.internal_profile)
        self.assertIn(self.internal_profile.name, html)
        # Internal → order-value columns, not commission
        self.assertIn("Valor mínimo do pedido", html)
        self.assertIn("Desconto máximo", html)
        self.assertNotIn("Comissão (%)", html)

    def test_report_loads_shared_style_kit(self):
        """Style kit marker proves ``tr_report_style.report_styles`` was called.

        If the consumer template forgets the ``t-call`` to the kit (or the kit
        module isn't installed) the marker class is missing and this test
        fails — even if the visual still looks right because of a leftover
        cached stylesheet.
        """
        html = self._render(self.agent_profile)
        self.assertIn("tr-report-style-loaded", html)
        # Document header now uses the shared ``.tr-doc-*`` classes
        self.assertIn('class="tr-doc-header"', html)
        self.assertIn('class="tr-doc-title"', html)

    def test_my_profiles_returns_user_profile(self):
        self.salesperson.partner_id.with_company(
            self.company
        ).sales_profile_id = self.agent_profile
        ids = (
            self.env["tr.sales.profile"]
            .with_user(self.salesperson)
            ._get_my_profile_ids()
        )
        self.assertEqual(ids, [self.agent_profile.id])

    def test_my_profiles_no_fallback_to_company_default(self):
        # User has NO profile of their own; company has default set.
        self.salesperson.partner_id.with_company(self.company).sales_profile_id = False
        self.company.default_sales_profile_id = self.internal_profile
        ids = (
            self.env["tr.sales.profile"]
            .with_user(self.salesperson)
            ._get_my_profile_ids()
        )
        self.assertEqual(ids, [])

    def test_my_profiles_multi_company(self):
        company_b = self.env["res.company"].create({"name": "Profile Report Company B"})
        # Allow the salesperson on both companies.
        self.salesperson.write({"company_ids": [(4, company_b.id)]})
        # Profile on company A only; B left empty.
        self.salesperson.partner_id.with_company(
            self.company
        ).sales_profile_id = self.agent_profile
        self.salesperson.partner_id.with_company(company_b).sales_profile_id = False
        ids = (
            self.env["tr.sales.profile"]
            .with_user(self.salesperson)
            .with_context(allowed_company_ids=[self.company.id, company_b.id])
            ._get_my_profile_ids()
        )
        self.assertEqual(ids, [self.agent_profile.id])

    def test_my_profiles_respects_active_companies_only(self):
        """User with profile in A and B but only A active in the session
        switcher must see only A's profile, not B's."""
        company_b = self.env["res.company"].create({"name": "Profile Report Company B"})
        self.salesperson.write({"company_ids": [(4, company_b.id)]})
        self.salesperson.partner_id.with_company(
            self.company
        ).sales_profile_id = self.agent_profile
        self.salesperson.partner_id.with_company(
            company_b
        ).sales_profile_id = self.internal_profile
        ids = (
            self.env["tr.sales.profile"]
            .with_user(self.salesperson)
            .with_context(allowed_company_ids=[self.company.id])
            ._get_my_profile_ids()
        )
        self.assertEqual(ids, [self.agent_profile.id])

    def test_action_my_profiles_returns_act_window(self):
        self.salesperson.partner_id.with_company(
            self.company
        ).sales_profile_id = self.agent_profile
        action = (
            self.env["tr.sales.profile"]
            .with_user(self.salesperson)
            .action_my_profiles()
        )
        self.assertEqual(action["res_model"], "tr.sales.profile")
        self.assertIn(("id", "in", [self.agent_profile.id]), action["domain"])

    def test_my_profiles_server_action_runs_for_non_director(self):
        """Regression: ir.actions.server.run() falls back to a write
        access check on the target model when groups_id is empty.
        Without explicit groups_id on the server action, a salesperson
        (no write on tr.sales.profile) gets AccessError when opening
        the 'My Commercial Profiles' menu — exactly the symptom the
        external sales rep hit in production.
        """
        self.salesperson.partner_id.with_company(
            self.company
        ).sales_profile_id = self.agent_profile
        server_action = self.env.ref(
            "tr_commercial_policy.tr_sales_profile_my_action_server"
        )
        result = server_action.with_user(self.salesperson).run()
        self.assertTrue(result)
        self.assertEqual(result["res_model"], "tr.sales.profile")
        # Domain calculated in the user's context, not sudo: the
        # salesperson's own profile is the one returned.
        self.assertIn(("id", "in", [self.agent_profile.id]), result["domain"])

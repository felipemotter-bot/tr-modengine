# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json

from lxml import etree

from odoo.tests import tagged

from .common import SalesRepAccessTestCommon

CONTACT_FIELDS = ("name", "phone", "mobile", "email")
# Address fields (street2, city, state_id, zip, country_id) and the
# BR split (street_name, street_number, district, city_id) are
# rendered via l10n_br_base's primary address view, outside of
# base.view_partner_form's inherit chain. They are out of scope for
# this PR — see the comment in views/res_partner_views.xml — and
# covered by a follow-up PR that inherits the primary view directly.


def _has_state_confirmed_readonly(node):
    """True when the node's compiled ``modifiers`` carry
    ``readonly`` with the exact clause ``('state', '=', 'confirmed')``.

    The resolved arch returned by ``fields_view_get`` stores the
    compiled form of ``attrs=...`` as a JSON ``modifiers`` attribute
    — a plain string match on ``attrs`` would miss it. We walk the
    ``readonly`` list and accept the condition if it appears anywhere,
    because other clauses (e.g. the upstream ``review_ids``
    tier-validation readonly) may be ORed in alongside ours.
    """
    raw = node.get("modifiers", "")
    if not raw:
        return False
    try:
        modifiers = json.loads(raw)
    except ValueError:
        return False
    readonly = modifiers.get("readonly")
    if not isinstance(readonly, list):
        return False
    return any(
        isinstance(cond, list)
        and len(cond) == 3
        and cond[0] == "state"
        and cond[1] == "="
        and cond[2] == "confirmed"
        for cond in readonly
    )


def _main_form_nodes(tree, fname):
    """Nodes for ``fname`` at the top-level partner form, excluding
    the embedded child_ids subtree.
    """
    return tree.xpath(
        "//field[@name=$fname][not(ancestor::field[@name='child_ids'])]",
        fname=fname,
    )


@tagged("post_install", "-at_install")
class TestRepPartnerReadonly(SalesRepAccessTestCommon):
    """Coverage for the PR 5b UX readonly-active on the partner form.

    Rep users see the top-level contact fields (name — both company
    and individual nodes — plus phone, mobile and email) flagged
    ``readonly`` when ``state == 'confirmed'``, so the form does not
    invite edits the PR 4b server-side guard would reject. Drafts
    stay editable (pre-approval flow of PR 3).

    Address block and nested child_ids form are out of scope — see
    the comment in views/res_partner_views.xml for why.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.active_stage = cls.env.ref("partner_stage.partner_stage_active")
        cls.draft_stage = cls.env.ref("partner_stage.partner_stage_draft")
        # Defensive: guarantee C1 is Active (matches PR 4b setup).
        cls.customer_c1.sudo().write({"stage_id": cls.active_stage.id})

    def _rep_arch_tree(self):
        view = (
            self.env["res.partner"]
            .with_user(self.user_u1)
            .fields_view_get(view_type="form")
        )
        return etree.fromstring(view["arch"])

    def _manager_arch_tree(self):
        admin = self.env.ref("base.user_admin")
        view = (
            self.env["res.partner"].with_user(admin).fields_view_get(view_type="form")
        )
        return etree.fromstring(view["arch"])

    # ------------------------------------------------------------------
    # Rep — main form
    # ------------------------------------------------------------------

    def test_rep_form_has_state_readonly_on_contact_fields(self):
        tree = self._rep_arch_tree()
        for fname in CONTACT_FIELDS:
            nodes = _main_form_nodes(tree, fname)
            self.assertTrue(nodes, f"main form is missing a {fname} node for the rep")
            matching = [n for n in nodes if _has_state_confirmed_readonly(n)]
            self.assertTrue(
                matching,
                f"rep form: no {fname} node carries state-confirmed readonly; "
                f"found {len(nodes)} node(s) without the modifier",
            )

    def test_rep_form_covers_both_name_nodes(self):
        # The top-level form declares two name nodes — one for company,
        # one for individual — and alternates visibility by is_company.
        # Both must carry the rep-specific readonly so the rule applies
        # regardless of which persona the partner is.
        tree = self._rep_arch_tree()
        name_nodes = _main_form_nodes(tree, "name")
        rep_nodes = [n for n in name_nodes if _has_state_confirmed_readonly(n)]
        # At least two rep name nodes — company-visible and
        # individual-visible — are expected at the top of the form.
        self.assertGreaterEqual(
            len(rep_nodes),
            2,
            f"expected both company and individual name nodes to carry the "
            f"rep readonly modifier, found {len(rep_nodes)}",
        )

    # ------------------------------------------------------------------
    # Manager regression
    # ------------------------------------------------------------------

    def test_manager_form_does_not_have_state_confirmed_readonly(self):
        # Manager may still see other readonly clauses upstream — the
        # core form already hangs readonly on ``review_ids != []`` for
        # the tier-validation flow. What must stay exclusive to the
        # rep is the clause conditioned on ``state == 'confirmed'``.
        tree = self._manager_arch_tree()
        for fname in CONTACT_FIELDS:
            for node in _main_form_nodes(tree, fname):
                self.assertFalse(
                    _has_state_confirmed_readonly(node),
                    f"manager form: {fname} must not carry the rep-specific "
                    f"state-confirmed readonly, but one node does",
                )

    # ------------------------------------------------------------------
    # Draft still editable by the rep
    # ------------------------------------------------------------------

    def test_draft_partner_still_editable_by_rep(self):
        # The readonly is bound to state == 'confirmed', not to the
        # presence of the rep group. A Draft partner must still accept
        # writes so the pre-approval flow from PR 3 keeps working.
        # Created via sudo to bypass the rep's ``create`` override
        # that forces Draft + tier validation — we want to isolate the
        # readonly attribute behaviour from the PR 3 workflow.
        draft = (
            self.env["res.partner"]
            .sudo()
            .create(
                {
                    "name": "Draft Customer PR5b",
                    "agent_ids": [(6, 0, [self.agent_a1.id])],
                    "stage_id": self.draft_stage.id,
                }
            )
        )
        # Rep can write on Draft — the PR 4b guard defers on
        # non-confirmed state, and the view readonly is state-scoped.
        draft.with_user(self.user_u1).write({"phone": "+55 11 0000-0000"})
        self.assertEqual(draft.phone, "+55 11 0000-0000")

    # ------------------------------------------------------------------
    # Helper defensive branches — exercised via synthetic nodes so the
    # early-return paths stay covered even though the real arch
    # produced by ``fields_view_get`` never hits them.
    # ------------------------------------------------------------------

    def test_helper_returns_false_on_empty_modifiers(self):
        # Arch serializers sometimes emit ``modifiers=""`` for fields
        # without any dynamic attr. The helper must treat that as
        # "no state-confirmed readonly" without trying to json-parse.
        node = etree.Element("field", name="phone", modifiers="")
        self.assertFalse(_has_state_confirmed_readonly(node))

    def test_helper_returns_false_on_invalid_json(self):
        # Defensive: if a future arch generator produces a non-JSON
        # modifiers payload, the helper falls back to False instead
        # of raising and masking the real regression.
        node = etree.Element("field", name="phone", modifiers="not-json")
        self.assertFalse(_has_state_confirmed_readonly(node))

    def test_helper_returns_false_when_readonly_not_list(self):
        # If ``readonly`` ever shows up as a scalar (e.g. plain ``True``
        # serialized as a JSON boolean), the helper keeps the
        # state-confirmed semantics strict — no implicit coercion.
        node = etree.Element("field", name="phone", modifiers='{"readonly": true}')
        self.assertFalse(_has_state_confirmed_readonly(node))

    # Nested child_ids form is out of scope for PR 5b — see the
    # comment in views/res_partner_views.xml. The PR 4b guard keeps
    # rep writes on child partners blocked server-side regardless.

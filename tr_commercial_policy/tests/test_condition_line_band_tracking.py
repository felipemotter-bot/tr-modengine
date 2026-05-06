# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import CommercialPolicyTestCommon


class TestConditionLineBandTracking(CommercialPolicyTestCommon):
    """Changes to condition lines and bands must surface on the
    parent condition's chatter — direct field tracking on the
    condition only catches scalar fields, not One2many edits."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()
        cls.uom_unit = cls.env.ref("uom.product_uom_unit")

    def _condition_messages(self):
        return self.condition.message_ids.sorted("id")

    def _last_body(self):
        msgs = self._condition_messages()
        return msgs[-1].body if msgs else ""

    def test_line_create_posts_message(self):
        before = len(self._condition_messages())
        self.env["partner.commercial.condition.line"].with_user(
            self.director_user
        ).create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product",
                "product_id": self.product_a.id,
                "seller_discount": 7.5,
            }
        )
        msgs = self._condition_messages()
        self.assertEqual(len(msgs), before + 1)
        body = msgs[-1].body
        self.assertIn("Product line added", body)
        self.assertIn(self.product_a.display_name, body)
        self.assertIn("7.50", body)

    def test_line_write_posts_diff(self):
        line = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.director_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product",
                    "product_id": self.product_a.id,
                    "seller_discount": 5.0,
                }
            )
        )
        before = len(self._condition_messages())
        line.with_user(self.director_user).write({"seller_discount": 12.0})
        msgs = self._condition_messages()
        self.assertEqual(len(msgs), before + 1)
        body = msgs[-1].body
        self.assertIn("Product line updated", body)
        self.assertIn("5.00", body)
        self.assertIn("12.00", body)

    def test_line_unlink_posts_message(self):
        line = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.director_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product",
                    "product_id": self.product_a.id,
                    "seller_discount": 5.0,
                }
            )
        )
        label = line._line_chatter_label()
        before = len(self._condition_messages())
        line.unlink()
        msgs = self._condition_messages()
        self.assertEqual(len(msgs), before + 1)
        body = msgs[-1].body
        self.assertIn("Product line removed", body)
        self.assertIn(label, body)

    def test_band_create_write_unlink_posts(self):
        line = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.director_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product",
                    "product_id": self.product_a.id,
                    "seller_discount": 5.0,
                }
            )
        )
        before = len(self._condition_messages())
        band = (
            self.env["partner.commercial.condition.line.band"]
            .with_user(self.director_user)
            .create(
                {
                    "line_id": line.id,
                    "qty_min": 50.0,
                    "qty_uom_id": self.product_a.uom_id.id,
                    "seller_discount": 15.0,
                }
            )
        )
        body = self._last_body()
        self.assertIn("Quantity band added", body)
        self.assertIn("15.00", body)

        band.with_user(self.director_user).write({"seller_discount": 20.0})
        body = self._last_body()
        self.assertIn("Quantity band updated", body)
        self.assertIn("15.00", body)
        self.assertIn("20.00", body)

        band.unlink()
        body = self._last_body()
        self.assertIn("Quantity band removed", body)
        self.assertGreaterEqual(len(self._condition_messages()), before + 3)

    def test_line_write_float_to_zero_renders_value(self):
        """0.0 must render as ``0.00``, not ``(empty)``.

        Regression: ``0.0 == False`` in Python made the formatter
        treat zeros as empty, hiding the new value on a 5% → 0%
        transition.
        """
        line = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.director_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product",
                    "product_id": self.product_a.id,
                    "seller_discount": 5.0,
                }
            )
        )
        line.with_user(self.director_user).write({"seller_discount": 0.0})
        body = self._last_body()
        self.assertIn("Product line updated", body)
        self.assertIn("5.00", body)
        self.assertIn("0.00", body)
        self.assertNotIn("(empty)", body)

    def test_band_write_qty_uom_change_posts(self):
        """Changing a band's m2o ``qty_uom_id`` is a tracked diff."""
        uom_dozen = self.env.ref("uom.product_uom_dozen")
        line = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.director_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product",
                    "product_id": self.product_a.id,
                    "seller_discount": 5.0,
                }
            )
        )
        band = (
            self.env["partner.commercial.condition.line.band"]
            .with_user(self.director_user)
            .create(
                {
                    "line_id": line.id,
                    "qty_min": 12.0,
                    "qty_uom_id": self.product_a.uom_id.id,
                    "seller_discount": 10.0,
                }
            )
        )
        before = len(self._condition_messages())
        band.with_user(self.director_user).write({"qty_uom_id": uom_dozen.id})
        body = self._last_body()
        self.assertEqual(len(self._condition_messages()), before + 1)
        self.assertIn("Quantity band updated", body)
        self.assertIn(self.product_a.uom_id.name, body)
        self.assertIn(uom_dozen.name, body)

    def test_band_write_no_change_no_message(self):
        """Writing the same qty_uom_id (m2o equality skip) posts nothing."""
        line = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.director_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product",
                    "product_id": self.product_a.id,
                    "seller_discount": 5.0,
                }
            )
        )
        band = (
            self.env["partner.commercial.condition.line.band"]
            .with_user(self.director_user)
            .create(
                {
                    "line_id": line.id,
                    "qty_min": 10.0,
                    "qty_uom_id": self.product_a.uom_id.id,
                    "seller_discount": 10.0,
                }
            )
        )
        before = len(self._condition_messages())
        band.with_user(self.director_user).write(
            {
                "qty_uom_id": self.product_a.uom_id.id,
                "seller_discount": 10.0,
            }
        )
        self.assertEqual(len(self._condition_messages()), before)

    def test_band_chatter_suppressed_by_context(self):
        """Suppression contexts must skip band create/write/unlink posts."""
        line = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.director_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product",
                    "product_id": self.product_a.id,
                    "seller_discount": 5.0,
                }
            )
        )
        Band = self.env["partner.commercial.condition.line.band"].with_user(
            self.director_user
        )
        before = len(self._condition_messages())
        band = Band.with_context(tracking_disable=True).create(
            {
                "line_id": line.id,
                "qty_min": 30.0,
                "qty_uom_id": self.product_a.uom_id.id,
                "seller_discount": 8.0,
            }
        )
        band.with_context(tracking_disable=True).write({"seller_discount": 9.0})
        band.with_context(tracking_disable=True).unlink()
        self.assertEqual(len(self._condition_messages()), before)

    def test_chatter_suppressed_by_context(self):
        """``tracking_disable`` / ``mail_notrack`` / ``mail_create_nolog``
        must skip line/band chatter posts."""
        Line = self.env["partner.commercial.condition.line"].with_user(
            self.director_user
        )
        for key in ("tracking_disable", "mail_notrack", "mail_create_nolog"):
            before = len(self._condition_messages())
            line = Line.with_context(**{key: True}).create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product",
                    "product_id": self.product_a.id,
                    "seller_discount": 3.0,
                }
            )
            line.with_context(**{key: True}).write({"seller_discount": 4.0})
            line.with_context(**{key: True}).unlink()
            self.assertEqual(
                len(self._condition_messages()),
                before,
                "context flag %r should suppress chatter posts" % key,
            )

    def test_one2many_commands_via_parent_post(self):
        """Editing line_ids/band_ids via parent commands still posts."""
        before = len(self._condition_messages())
        self.condition.with_user(self.director_user).write(
            {
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "product",
                            "product_id": self.product_a.id,
                            "seller_discount": 8.0,
                            "band_ids": [
                                (
                                    0,
                                    0,
                                    {
                                        "qty_min": 100.0,
                                        "qty_uom_id": self.product_a.uom_id.id,
                                        "seller_discount": 10.0,
                                    },
                                )
                            ],
                        },
                    )
                ]
            }
        )
        bodies = "\n".join(m.body for m in self._condition_messages())
        self.assertIn("Product line added", bodies)
        self.assertIn("Quantity band added", bodies)
        self.assertGreater(len(self._condition_messages()), before)

    def test_line_create_skips_empty_fields_in_log(self):
        """Empty m2o / zero float fields must not appear as
        ``(empty) → (empty)`` on the create message — only fields
        that were actually set should be listed."""
        line = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.director_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product",
                    "product_id": self.product_a.id,
                    "seller_discount": 4.0,
                }
            )
        )
        body = self._last_body()
        self.assertIn("Product line added", body)
        # Variant line: product_tmpl_id is empty, must not be listed.
        self.assertNotIn("Product Template", body)
        self.assertNotIn("Modelo de Produto", body)
        # Generic guard against a regression where both sides resolve
        # to empty (the original Felipe-reported case).
        self.assertNotIn("(empty) → (empty)", body)
        # Sanity: line is correctly attached to the variant path.
        self.assertEqual(line.applied_on, "product")

    def test_field_label_helper_uses_fields_get(self):
        """``_field_label`` must go through ``fields_get`` so the
        chatter picks up the user's language. Reading
        ``record._fields[fname].string`` would always return the
        Python source label.
        """
        from ..models.partner_commercial_condition import _field_label

        Line = self.env["partner.commercial.condition.line"]
        line = Line.with_user(self.director_user).create(
            {
                "condition_id": self.condition.id,
                "applied_on": "product",
                "product_id": self.product_a.id,
                "seller_discount": 4.0,
            }
        )
        expected = line.fields_get(["seller_discount"])["seller_discount"]["string"]
        self.assertEqual(_field_label(line, "seller_discount"), expected)

    def test_line_write_no_tracked_change_no_message(self):
        line = (
            self.env["partner.commercial.condition.line"]
            .with_user(self.director_user)
            .create(
                {
                    "condition_id": self.condition.id,
                    "applied_on": "product",
                    "product_id": self.product_a.id,
                    "seller_discount": 5.0,
                }
            )
        )
        before = len(self._condition_messages())
        line.with_user(self.director_user).write({"seller_discount": 5.0})
        self.assertEqual(len(self._condition_messages()), before)

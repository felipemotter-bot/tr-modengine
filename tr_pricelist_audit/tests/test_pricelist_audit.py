from odoo.tests.common import TransactionCase


class TestPricelistAudit(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Pricelist = cls.env["product.pricelist"]
        cls.Item = cls.env["product.pricelist.item"]
        cls.pricelist = cls.Pricelist.create({"name": "Audit PL"})
        cls.pricelist2 = cls.Pricelist.create({"name": "Audit PL 2"})
        # mail.thread.create calls _track_discard, which would suppress
        # tracking on subsequent writes within the same transaction. Run
        # the precommit hooks now to clear the discard state.
        cls.env.cr.precommit.run()

    def _flush(self):
        self.env.flush_all()
        self.env.cr.precommit.run()
        self.env.flush_all()

    def _msg_bodies(self, pricelist):
        self._flush()
        pricelist.invalidate_cache(["message_ids"])
        return [m.body for m in pricelist.message_ids]

    def _tracking_fields(self, pricelist):
        self._flush()
        pricelist.invalidate_cache(["message_ids"])
        return [
            tv.field.name for m in pricelist.message_ids for tv in m.tracking_value_ids
        ]

    # 1
    def test_pricelist_tracking_name(self):
        self.pricelist.write({"name": "Audit PL Renamed"})
        self.assertIn("name", self._tracking_fields(self.pricelist))

    # 2
    def test_pricelist_tracking_company(self):
        company = self.env["res.company"].create({"name": "Audit Co"})
        self.pricelist.write({"company_id": company.id})
        self.assertIn("company_id", self._tracking_fields(self.pricelist))

    # 3
    def test_item_create_logs_message(self):
        before = len(self.pricelist.message_ids)
        self.Item.create({"pricelist_id": self.pricelist.id, "fixed_price": 10.0})
        after_bodies = self._msg_bodies(self.pricelist)
        self.assertGreater(len(after_bodies), before)
        self.assertTrue(any("Line added" in str(b) for b in after_bodies))

    # 4
    def test_item_write_logs_changed_fields(self):
        item = self.Item.create(
            {"pricelist_id": self.pricelist.id, "fixed_price": 10.0}
        )
        before = len(self.pricelist.message_ids)
        item.write({"fixed_price": 25.0})
        bodies = self._msg_bodies(self.pricelist)
        self.assertGreater(len(bodies), before)
        latest = str(bodies[0])
        self.assertIn("Line updated", latest)
        self.assertIn("10", latest)
        self.assertIn("25", latest)

    # 5
    def test_item_write_ignores_irrelevant_fields(self):
        item = self.Item.create(
            {"pricelist_id": self.pricelist.id, "fixed_price": 10.0}
        )
        before = len(self.pricelist.message_ids)
        item.write({"name": "irrelevant tweak"})
        self.assertEqual(len(self._msg_bodies(self.pricelist)), before)

    # 6
    def test_item_write_same_relevant_value_does_not_log(self):
        item = self.Item.create(
            {"pricelist_id": self.pricelist.id, "fixed_price": 10.0}
        )
        before = len(self.pricelist.message_ids)
        item.write({"fixed_price": 10.0})
        self.assertEqual(len(self._msg_bodies(self.pricelist)), before)

    # 7
    def test_item_write_pricelist_id_logs_old_and_new_pricelist(self):
        item = self.Item.create(
            {"pricelist_id": self.pricelist.id, "fixed_price": 10.0}
        )
        before_old = len(self.pricelist.message_ids)
        before_new = len(self.pricelist2.message_ids)
        item.write({"pricelist_id": self.pricelist2.id})
        self.assertGreater(len(self._msg_bodies(self.pricelist)), before_old)
        self.assertGreater(len(self._msg_bodies(self.pricelist2)), before_new)
        self.assertTrue(
            any("moved to" in str(b) for b in self._msg_bodies(self.pricelist))
        )
        self.assertTrue(
            any("moved from" in str(b) for b in self._msg_bodies(self.pricelist2))
        )

    # 8
    def test_item_unlink_logs_message(self):
        item = self.Item.create(
            {"pricelist_id": self.pricelist.id, "fixed_price": 10.0}
        )
        before = len(self.pricelist.message_ids)
        item.unlink()
        bodies = self._msg_bodies(self.pricelist)
        self.assertGreater(len(bodies), before)
        self.assertTrue(any("Line removed" in str(b) for b in bodies))

    # 9
    def test_item_write_batch_under_threshold(self):
        items = self.Item.create(
            [
                {"pricelist_id": self.pricelist.id, "fixed_price": float(i)}
                for i in range(1, 6)
            ]
        )
        before = len(self.pricelist.message_ids)
        items.write({"fixed_price": 99.0})
        new_messages = len(self.pricelist.message_ids) - before
        self.assertEqual(new_messages, 5)

    # extra: description branches (category / product / variant)
    def test_item_create_category_rule_logs_category_name(self):
        categ = self.env["product.category"].create({"name": "Audit Cat"})
        self.Item.create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "2_product_category",
                "categ_id": categ.id,
                "fixed_price": 5.0,
            }
        )
        latest = str(self._msg_bodies(self.pricelist)[0])
        self.assertIn("Category: Audit Cat", latest)

    def test_item_create_product_rule_logs_template_name(self):
        tmpl = self.env["product.template"].create({"name": "Audit Tmpl"})
        self.Item.create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "1_product",
                "product_tmpl_id": tmpl.id,
                "fixed_price": 5.0,
            }
        )
        latest = str(self._msg_bodies(self.pricelist)[0])
        self.assertIn("Product: Audit Tmpl", latest)

    def test_item_description_fallback_when_target_missing(self):
        # Cover the fallback branch in _audit_description when applied_on
        # would normally require a target (categ_id / product_tmpl_id /
        # product_id) but the target is empty. Default applied_on is
        # 3_global; force the inconsistent state via direct SQL UPDATE to
        # bypass onchange so the helper hits the fallback.
        item = self.Item.create({"pricelist_id": self.pricelist.id, "fixed_price": 5.0})
        self.env.cr.execute(
            "UPDATE product_pricelist_item SET applied_on=%s, categ_id=NULL,"
            " product_tmpl_id=NULL, product_id=NULL WHERE id=%s",
            ("2_product_category", item.id),
        )
        item.invalidate_recordset()
        self.assertEqual(item._audit_description(), "Rule #%d" % item.id)

    def test_item_create_variant_rule_logs_variant_name(self):
        tmpl = self.env["product.template"].create({"name": "Audit Var Tmpl"})
        variant = tmpl.product_variant_ids[:1]
        self.Item.create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "0_product_variant",
                "product_id": variant.id,
                "fixed_price": 5.0,
            }
        )
        latest = str(self._msg_bodies(self.pricelist)[0])
        self.assertIn("Variant: Audit Var Tmpl", latest)

    def test_item_write_clears_many2one_logs_empty(self):
        categ = self.env["product.category"].create({"name": "Cat X"})
        item = self.Item.create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "2_product_category",
                "categ_id": categ.id,
                "fixed_price": 5.0,
            }
        )
        item.write({"categ_id": False, "applied_on": "3_global"})
        latest = str(self._msg_bodies(self.pricelist)[0])
        self.assertIn("Cat X", latest)
        self.assertIn("(empty)", latest)

    def test_item_write_sets_date_logs_empty_to_value(self):
        item = self.Item.create({"pricelist_id": self.pricelist.id, "fixed_price": 5.0})
        item.write({"date_start": "2026-01-01"})
        latest = str(self._msg_bodies(self.pricelist)[0])
        self.assertIn("(empty)", latest)
        self.assertIn("2026-01-01", latest)

    def test_item_write_logs_many2one_change(self):
        categ1 = self.env["product.category"].create({"name": "Cat A"})
        categ2 = self.env["product.category"].create({"name": "Cat B"})
        item = self.Item.create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "2_product_category",
                "categ_id": categ1.id,
                "fixed_price": 5.0,
            }
        )
        item.write({"categ_id": categ2.id})
        latest = str(self._msg_bodies(self.pricelist)[0])
        self.assertIn("Cat A", latest)
        self.assertIn("Cat B", latest)

    # extra: percentage create
    def test_item_create_percentage_logs_percent_price(self):
        self.Item.create(
            {
                "pricelist_id": self.pricelist.id,
                "compute_price": "percentage",
                "percent_price": 15.0,
            }
        )
        bodies = self._msg_bodies(self.pricelist)
        latest = str(bodies[0])
        self.assertIn("Line added", latest)
        self.assertIn("15", latest)
        self.assertIn("Percentage", latest)

    # extra: percentage unlink
    def test_item_unlink_percentage_logs_removal(self):
        item = self.Item.create(
            {
                "pricelist_id": self.pricelist.id,
                "compute_price": "percentage",
                "percent_price": 22.0,
            }
        )
        item.unlink()
        latest = str(self._msg_bodies(self.pricelist)[0])
        self.assertIn("Line removed", latest)

    # extra: move + field change in same write
    def test_item_write_move_plus_field_change(self):
        item = self.Item.create(
            {"pricelist_id": self.pricelist.id, "fixed_price": 10.0}
        )
        item.write({"pricelist_id": self.pricelist2.id, "fixed_price": 50.0})
        new_bodies = [str(b) for b in self._msg_bodies(self.pricelist2)]
        joined = " ".join(new_bodies)
        self.assertIn("moved from", joined)
        self.assertIn("10", joined)
        self.assertIn("50", joined)

    # 10
    def test_item_write_batch_over_threshold(self):
        items = self.Item.create(
            [
                {"pricelist_id": self.pricelist.id, "fixed_price": float(i)}
                for i in range(1, 30)
            ]
        )
        before = len(self.pricelist.message_ids)
        items.write({"fixed_price": 77.0})
        new_messages = len(self.pricelist.message_ids) - before
        self.assertEqual(new_messages, 1)
        latest = str(self.pricelist.message_ids[0].body)
        self.assertIn("Batch change", latest)
        # diff content preserved inside the grouped message
        self.assertIn("Line updated", latest)
        self.assertIn("77", latest)

    def test_item_create_batch_over_threshold_preserves_values(self):
        before = len(self.pricelist.message_ids)
        self.Item.create(
            [
                {"pricelist_id": self.pricelist.id, "fixed_price": float(i)}
                for i in range(1, 30)
            ]
        )
        new_messages = len(self.pricelist.message_ids) - before
        self.assertEqual(new_messages, 1)
        latest = str(self.pricelist.message_ids[0].body)
        self.assertIn("Batch change", latest)
        self.assertIn("Line added", latest)
        # at least one of the fixed prices shows up in the bulleted values
        self.assertIn("29.00", latest)

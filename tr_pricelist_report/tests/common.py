# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.addons.tr_commercial_policy.tests.common import (
    CommercialPolicyTestCommon,
)


class PricelistReportTestCommon(CommercialPolicyTestCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_commercial_policy()

        # Pricelist items for products A and B so the pricelist chain
        # returns a deterministic base price.
        cls.item_a = cls.env["product.pricelist.item"].create(
            {
                "pricelist_id": cls.pricelist.id,
                "applied_on": "1_product",
                "product_tmpl_id": cls.product_template_a.id,
                "compute_price": "fixed",
                "fixed_price": 100.0,
            }
        )
        cls.item_b = cls.env["product.pricelist.item"].create(
            {
                "pricelist_id": cls.pricelist.id,
                "applied_on": "1_product",
                "product_tmpl_id": cls.product_template_b.id,
                "compute_price": "fixed",
                "fixed_price": 200.0,
            }
        )

    @classmethod
    def _open_wizard(cls, category_ids=None, **vals):
        category_ids = category_ids or [cls.categ_chemicals.id]
        wizard_vals = {
            "condition_id": cls.condition.id,
            "category_ids": [(6, 0, category_ids)],
        }
        wizard_vals.update(vals)
        return cls.env["tr.pricelist.report.wizard"].create(wizard_vals)

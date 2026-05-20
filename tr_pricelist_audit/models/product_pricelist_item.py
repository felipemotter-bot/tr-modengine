from collections import defaultdict

from markupsafe import escape as html_escape

from odoo import _, api, models
from odoo.tools import float_compare
from odoo.tools.misc import formatLang

BATCH_THRESHOLD = 20

RELEVANT_FIELDS = (
    "applied_on",
    "product_tmpl_id",
    "product_id",
    "categ_id",
    "compute_price",
    "fixed_price",
    "percent_price",
    "min_quantity",
    "date_start",
    "date_end",
)

FLOAT_FIELDS = ("fixed_price", "percent_price", "min_quantity")
FLOAT_PRECISION = 4


def _field_label(record, fname):
    return record.fields_get([fname])[fname]["string"]


def _format_value(record, fname, value):
    field = record._fields[fname]
    if field.type == "float":
        return formatLang(record.env, value or 0.0, digits=2)
    if field.type == "many2one":
        if not value:
            return _("(empty)")
        if isinstance(value, int):
            return record.env[field.comodel_name].browse(value).display_name
        return value.display_name
    if field.type in ("date", "datetime"):
        return str(value) if value else _("(empty)")
    # selection — applied_on / compute_price are required, always set.
    return dict(field._description_selection(record.env)).get(value, str(value))


def _render_values_html(values):
    rows = "".join(
        "<li><b>%s</b>: %s</li>" % (html_escape(label), html_escape(value))
        for (label, value) in values
    )
    return "<ul>%s</ul>" % rows


def _render_changes_html(changes):
    rows = "".join(
        "<li><b>%s</b>: %s → %s</li>"
        % (html_escape(label), html_escape(old), html_escape(new))
        for (label, old, new) in changes
    )
    return "<ul>%s</ul>" % rows


def _post_log(pricelist, header, body_extra=""):
    pricelist._message_log(body="<p>%s</p>%s" % (html_escape(header), body_extra))


class ProductPricelistItem(models.Model):
    _inherit = "product.pricelist.item"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _audit_description(self):
        self.ensure_one()
        if self.applied_on == "3_global":
            return _("Global")
        if self.applied_on == "2_product_category" and self.categ_id:
            return _("Category: %(name)s", name=self.categ_id.display_name)
        if self.applied_on == "1_product" and self.product_tmpl_id:
            return _("Product: %(name)s", name=self.product_tmpl_id.display_name)
        if self.applied_on == "0_product_variant" and self.product_id:
            return _("Variant: %(name)s", name=self.product_id.display_name)
        return _("Rule #%(rid)s", rid=self.id)

    def _audit_snapshot(self):
        self.ensure_one()
        snap = {}
        for fname in RELEVANT_FIELDS:
            value = self[fname]
            field = self._fields[fname]
            if field.type == "many2one":
                snap[fname] = value.id
            else:
                snap[fname] = value
        snap["_pricelist_id"] = self.pricelist_id.id
        snap["_description"] = self._audit_description()
        return snap

    @staticmethod
    def _audit_field_equal(field_type, old, new):
        if field_type == "float":
            return (
                float_compare(old or 0.0, new or 0.0, precision_digits=FLOAT_PRECISION)
                == 0
            )
        return old == new

    def _audit_values(self):
        """Return [(label, value), ...] of relevant fields with content."""
        self.ensure_one()
        result = []
        for fname in RELEVANT_FIELDS:
            value = self[fname]
            if not value and value != 0.0:
                continue
            if self._fields[fname].type == "float" and not value:
                continue
            result.append(
                (_field_label(self, fname), _format_value(self, fname, value))
            )
        return result

    def _audit_changes(self, old_snap):
        """Return [(label, old, new), ...] for fields that changed."""
        self.ensure_one()
        out = []
        for fname in RELEVANT_FIELDS:
            field = self._fields[fname]
            new_val = self[fname]
            new_compare = new_val.id if field.type == "many2one" else new_val
            if self._audit_field_equal(field.type, old_snap[fname], new_compare):
                continue
            out.append(
                (
                    _field_label(self, fname),
                    _format_value(self, fname, old_snap[fname]),
                    _format_value(self, fname, new_val),
                )
            )
        return out

    def _audit_emit(self, by_pricelist):
        """by_pricelist: {pricelist_id: [(header, body_extra), ...]}"""
        for pricelist_id, entries in by_pricelist.items():
            pricelist = self.env["product.pricelist"].browse(pricelist_id)
            if len(entries) > BATCH_THRESHOLD:
                bullets = "".join(
                    "<li><p>%s</p>%s</li>" % (html_escape(h), b or "")
                    for h, b in entries
                )
                body = "<p>%s</p><ul>%s</ul>" % (
                    html_escape(
                        _("Batch change on %(n)d pricelist rules", n=len(entries))
                    ),
                    bullets,
                )
                pricelist._message_log(body=body)
            else:
                for header, body_extra in entries:
                    _post_log(pricelist, header, body_extra or "")

    # ------------------------------------------------------------------
    # CRUD overrides
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        by_pricelist = defaultdict(list)
        for rec in records:
            header = _("Line added: %(desc)s", desc=rec._audit_description())
            body_extra = _render_values_html(rec._audit_values())
            by_pricelist[rec.pricelist_id.id].append((header, body_extra))
        self._audit_emit(by_pricelist)
        return records

    def write(self, vals):
        old_snaps = {rec.id: rec._audit_snapshot() for rec in self}
        res = super().write(vals)
        by_pricelist = defaultdict(list)
        for rec in self:
            old = old_snaps[rec.id]
            old_pricelist = old["_pricelist_id"]
            new_pricelist = rec.pricelist_id.id
            description = rec._audit_description()
            changes = rec._audit_changes(old)

            if old_pricelist and new_pricelist and old_pricelist != new_pricelist:
                new_name = rec.pricelist_id.display_name
                old_name = (
                    self.env["product.pricelist"].browse(old_pricelist).display_name
                )
                by_pricelist[old_pricelist].append(
                    (
                        _(
                            "Line removed (moved to %(target)s): %(desc)s",
                            target=new_name,
                            desc=old["_description"],
                        ),
                        "",
                    )
                )
                by_pricelist[new_pricelist].append(
                    (
                        _(
                            "Line added (moved from %(origin)s): %(desc)s",
                            origin=old_name,
                            desc=description,
                        ),
                        _render_changes_html(changes) if changes else "",
                    )
                )
                continue

            if changes and new_pricelist:
                by_pricelist[new_pricelist].append(
                    (
                        _("Line updated: %(desc)s", desc=description),
                        _render_changes_html(changes),
                    )
                )

        self._audit_emit(by_pricelist)
        return res

    def unlink(self):
        snaps = [rec._audit_snapshot() for rec in self]
        res = super().unlink()
        by_pricelist = defaultdict(list)
        for snap in snaps:
            pricelist_id = snap["_pricelist_id"]
            header = _("Line removed: %(desc)s", desc=snap["_description"])
            by_pricelist[pricelist_id].append((header, ""))
        self._audit_emit(by_pricelist)
        return res

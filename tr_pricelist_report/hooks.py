# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

# IDs of bindings and menu that belong to ``product_pricelist_direct_print``
# (OCA). Desactivated only when that module is installed, so the module is
# a soft dependency: we don't force its presence but we clean up its UI
# entry points when it's there, since they would bypass the commercial
# policy.
_OCA_BINDINGS = [
    "product_pricelist_direct_print.action_product_template_pricelist_print",
    "product_pricelist_direct_print.action_product_product_pricelist_print",
    "product_pricelist_direct_print.action_product_pricelist_print",
    "product_pricelist_direct_print.action_partner_pricelist_print",
    "product_pricelist_direct_print.action_item_pricelist_print",
    "product_pricelist_direct_print.action_pricelist_print",
]
_OCA_MENU = "product_pricelist_direct_print.menu_product_pricelist_print"
_OCA_MODULE = "product_pricelist_direct_print"


def _is_oca_installed(env):
    """Return True when ``product_pricelist_direct_print`` is installed."""
    return bool(
        env["ir.module.module"].search(
            [("name", "=", _OCA_MODULE), ("state", "=", "installed")],
            limit=1,
        )
    )


def _resolve_group_attribute(env):
    """Cache the id of the ``product.attribute`` used as the MARCA axis.

    The attribute is configurable by name via
    ``tr_pricelist_report.group_attribute_name`` (default ``"MARCA"``).
    Resolved once at install and cached in
    ``tr_pricelist_report.group_attribute_id`` so the report doesn't
    search by name on every render. If no attribute matches, the id is
    left empty and the Modo A falls back to "no MARCA" (all products
    routed through the category fallback resolver).
    """
    icp = env["ir.config_parameter"].sudo()
    name = icp.get_param("tr_pricelist_report.group_attribute_name", "MARCA")
    attribute = env["product.attribute"].search([("name", "=", name)], limit=1)
    icp.set_param(
        "tr_pricelist_report.group_attribute_id", str(attribute.id) if attribute else ""
    )
    if attribute:
        _logger.info(
            "tr_pricelist_report: resolved group attribute %r to id %s",
            name,
            attribute.id,
        )
    else:
        _logger.info(
            "tr_pricelist_report: no product.attribute named %r, Modo A will "
            "treat every product as having no MARCA",
            name,
        )


def post_init_hook(cr, registry):
    """Post-install setup.

    Two responsibilities:

    1. Resolve the ``group_attribute_id`` from the configured name.
    2. Soft-disable the OCA ``product_pricelist_direct_print`` bindings
       when that module is installed.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    _resolve_group_attribute(env)
    if not _is_oca_installed(env):
        return
    for xml_id in _OCA_BINDINGS:
        action = env.ref(xml_id, raise_if_not_found=False)
        if action:
            action.write({"binding_model_id": False, "binding_view_types": False})
    menu = env.ref(_OCA_MENU, raise_if_not_found=False)
    if menu:
        menu.active = False
    _logger.info(
        "tr_pricelist_report: disabled bindings/menu of %s",
        _OCA_MODULE,
    )

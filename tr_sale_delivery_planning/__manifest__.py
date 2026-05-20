# Copyright 2026 Engenere - Felipe Motter Pereira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Sale Delivery Planning",
    "version": "16.0.1.0.0",
    "maintainers": ["felipemotter"],
    "author": "Engenere",
    "license": "AGPL-3",
    "category": "Inventory/Delivery",
    "development_status": "Alpha",
    "summary": "Plan sale delivery dates on stock pickings with a dedicated "
    "planner role and noise-free integration with native lateness views.",
    "website": "https://github.com/Engenere/addons-trento",
    "depends": [
        "sale_stock",
        "stock",
    ],
    "data": [
        "security/security.xml",
        "views/stock_picking_views.xml",
        "views/sale_order_views.xml",
    ],
    "installable": True,
}

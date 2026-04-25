{
    "name": "Trento Invoice Discount Snapshot",
    "summary": (
        "Propaga desconto da política comercial para a linha da fatura "
        "independente de configuração fiscal."
    ),
    "version": "16.0.1.0.0",
    "category": "Accounting",
    "author": "Engenere",
    "website": "https://github.com/Engenere/addons-trento",
    "license": "AGPL-3",
    "depends": [
        "tr_commercial_policy",
        "l10n_br_account",
    ],
    "pre_init_hook": "pre_init_hook",
    "post_init_hook": "post_init_hook",
    "installable": True,
    "auto_install": False,
}

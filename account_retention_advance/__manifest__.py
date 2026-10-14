{
    'name': 'account retention advance',
    'version': '19.0.0.0',
    'author': 'Machinser Group',
    'company': 'Machinser Group',
    'website': "",
    'depends': ['base', 'sale_management','accountant','l10n_sa'],
    'description':"Manage customer invoice retention",
    'data': [
                'views/res_company.xml',
                'views/res_config_settings.xml',
                'views/account_move.xml',
    ],
    'license': 'AGPL-3',
    'installable': True,
    'auto_install': False,
    'application': True,
}

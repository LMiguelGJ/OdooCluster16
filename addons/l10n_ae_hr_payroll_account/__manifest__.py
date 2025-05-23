# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name': 'United Arab Emirates - Payroll with Accounting',
    'author': 'Odoo S.A.',
    'category': 'Human Resources',
    'description': """
Accounting Data for UAE Payroll Rules.
=======================================================

    """,
    'depends': ['hr_payroll_account', 'l10n_ae', 'l10n_ae_hr_payroll'],
    'data': [
        'data/account_chart_template_data.xml',
        'data/hr_payroll_structure_data.xml',
    ],
    'license': 'OEEL-1',
    'auto_install': True,
}

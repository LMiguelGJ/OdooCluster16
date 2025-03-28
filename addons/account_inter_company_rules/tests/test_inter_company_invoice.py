# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
import odoo.tests
from .common import TestInterCompanyRulesCommon


@odoo.tests.tagged('post_install','-at_install')
class TestInterCompanyInvoice(TestInterCompanyRulesCommon):

    @classmethod
    def setUpClass(cls):
        super(TestInterCompanyInvoice, cls).setUpClass()
        # Enable auto generate invoice in company.
        (cls.company_a + cls.company_b).write({
            'rule_type': 'invoice_and_refund'
        })
        # Configure Chart of Account for company_b.
        cls.env.user.company_id = cls.company_b
        cls.env['account.chart.template'].browse(1).with_company(cls.company_b).try_loading()

        # Configure Chart of Account for company_a.
        cls.env.user.company_id = cls.company_a
        cls.env['account.chart.template'].browse(1).with_company(cls.company_a).try_loading()

    def _configure_analytic(self, product=None, company=None, partner=None):
        """
        Configure Analytic Distribution Model for company_a based on Product A
        return: analytic account
        """
        display_name = "Inter Company"
        if company:
            self.env.user.company_id = company
            display_name = company.display_name
        analytic_plan = self.env['account.analytic.plan'].create({'name': f'Analytic Plan {display_name}', 'company_id': company and company.id})
        analytic_account = self.env['account.analytic.account'].create({
            'name': f'Account {display_name}',
            'company_id': company and company.id,
            'plan_id': analytic_plan.id,
        })
        self.env['account.analytic.distribution.model'].create({
            'analytic_distribution': {analytic_account.id: 100},
            'product_id': product and product.id,
            'company_id': company and company.id,
            'partner_id': partner and partner.id,
        })
        return analytic_account

    def _create_post_invoice(self, product_id, analytic_distribution=None):
        """Create a Company A invoice with Company B as the customer and post it"""
        invoice_line_vals = {
                'product_id': product_id,
                'price_unit': 100.0,
                'quantity': 1.0,
            }
        if analytic_distribution:
            invoice_line_vals['analytic_distribution'] = analytic_distribution

        customer_invoice = self.env['account.move'].with_user(self.res_users_company_a).create({
            'move_type': 'out_invoice',
            'partner_id': self.company_b.partner_id.id,
            'invoice_line_ids': [(0, 0, invoice_line_vals)]
        })
        customer_invoice.with_user(self.res_users_company_a).action_post()

    def test_00_inter_company_invoice_flow(self):
        """ Test inter company invoice flow """

        self.env.ref('base.EUR').active = True

        # Create customer invoice for company A. (No need to call onchange as all the needed values are specified)
        self.res_users_company_a.company_ids = [(4, self.company_b.id)]
        customer_invoice = self.env['account.move'].with_user(self.res_users_company_a).create({
            'move_type': 'out_invoice',
            'partner_id': self.company_b.partner_id.id,
            'currency_id': self.env.ref('base.EUR').id,
            'invoice_line_ids': [(0, 0, {
                'product_id': self.product_consultant.id,
                'price_unit': 450.0,
                'quantity': 1.0,
                'name': 'test'
            })]
        })

        # Check account invoice state should be draft.
        self.assertEqual(customer_invoice.state, 'draft', 'Initially customer invoice should be in the "Draft" state')

        # Validate invoice
        customer_invoice.with_user(self.res_users_company_a).action_post()

        # Check Invoice status should be open after validate.
        self.assertEqual(customer_invoice.state, 'posted', 'Invoice should be in Open state.')

        # I check that the vendor bill is created with proper data.
        supplier_invoice = self.env['account.move'].with_user(self.res_users_company_b).search([('move_type', '=', 'in_invoice')], limit=1)

        self.assertTrue(supplier_invoice.invoice_line_ids[0].quantity == 1, "Quantity in invoice line is incorrect.")
        self.assertTrue(supplier_invoice.invoice_line_ids[0].product_id.id == self.product_consultant.id, "Product in line is incorrect.")
        self.assertTrue(supplier_invoice.invoice_line_ids[0].price_unit == 450, "Unit Price in invoice line is incorrect.")
        self.assertTrue(supplier_invoice.invoice_line_ids[0].account_id.company_id.id == self.company_b.id, "Applied account in created invoice line is not relevant to company.")
        self.assertTrue(supplier_invoice.state == "draft", "invoice should be in draft state.")
        self.assertEqual(supplier_invoice.amount_total, 517.5, "Total amount is incorrect.")
        self.assertTrue(supplier_invoice.company_id.id == self.company_b.id, "Applied company in created invoice is incorrect.")

    def test_default_analytic_distribution_company_b(self):
        """
        [Analytic Distribution Model is set for Company B + Inter Company Analytic Account is set]
        - With Company A, create an Invoice for Company B with product A set with an analytic distribution model available for Company B
        -> The Analytic Distribution set on the Supplier Invoice Line should be the same as defined in the analytic distribution model set by default for Company B
        and the one manually set when the analytic account is also available for Company B
        """
        analytic_account_company_b = self._configure_analytic(company=self.company_b, product=self.product_a)
        inter_company_analytic_account = self._configure_analytic(product=self.product_b)

        self._create_post_invoice(product_id=self.product_a.id, analytic_distribution={inter_company_analytic_account.id: 100})
        supplier_invoice = self.env['account.move'].with_user(self.res_users_company_b).search([('move_type', '=', 'in_invoice')], limit=1)

        self.assertEqual(supplier_invoice.invoice_line_ids.analytic_distribution, {str(analytic_account_company_b.id): 100, str(inter_company_analytic_account.id): 100})

    def test_no_default_analytic_distribution_company_b(self):
        """
        [Analytic Distribution Model is not set for Company B + Inter Company Analytic Account is set]
        - With Company A, create an Invoice for Company B with a line set with an analytic distribution model available for Company B
        -> The analytic distribution set on the supplier invoice line should be the same as defined in the customer invoice line created in Company A
        as the analytic account is available for Company B and there are no default analytic distribution model set for Company B
        """
        inter_company_analytic_account = self._configure_analytic(product=self.product_b)

        self._create_post_invoice(product_id=self.product_a.id, analytic_distribution={inter_company_analytic_account.id: 100})
        supplier_invoice = self.env['account.move'].with_user(self.res_users_company_b).search([('move_type', '=', 'in_invoice')], limit=1)

        self.assertEqual(supplier_invoice.invoice_line_ids.analytic_distribution, {str(inter_company_analytic_account.id): 100})

    def test_default_analytic_distribution_company_a(self):
        """
        [Analytic Distribution Model is set for Company A]
        - With Company A, create an Invoice for Company B with a line set with an analytic distribution model not available for Company B
        -> There should be no analytic distribution set on the supplier invoice line as there is no analytic distribution model available for Company B
        and the analytic account is not available for Company B
        """
        analytic_account_company_a = self._configure_analytic(company=self.company_a, product=self.product_a)

        self._create_post_invoice(product_id=self.product_a.id, analytic_distribution={analytic_account_company_a.id: 100})
        supplier_invoice = self.env['account.move'].with_user(self.res_users_company_b).search([('move_type', '=', 'in_invoice')], limit=1)

        self.assertFalse(supplier_invoice.invoice_line_ids.analytic_distribution, "Analytic distribution should not be set on the invoice line.")

    def test_analytic_distribution_model_partner(self):
        """
        If company B defines Company A as a partner in its distribution model, the distribution should be retrieved
        """
        inter_company_analytic_account = self._configure_analytic(product=self.product_b)
        analytic_account_company_b = self._configure_analytic(company=self.company_b, partner=self.company_a.partner_id)
        self._create_post_invoice(product_id=self.product_a.id, analytic_distribution={inter_company_analytic_account.id: 100})

        self._create_post_invoice(product_id=self.product_a.id)
        supplier_invoice = self.env['account.move'].with_user(self.res_users_company_b).search([('move_type', '=', 'in_invoice')], limit=1)

        expected_distribution = {
            str(inter_company_analytic_account.id): 100,
            str(analytic_account_company_b.id): 100
        }
        self.assertEqual(supplier_invoice.invoice_line_ids.analytic_distribution, expected_distribution)

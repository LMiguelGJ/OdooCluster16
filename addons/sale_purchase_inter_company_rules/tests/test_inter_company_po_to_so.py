# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from .common import TestInterCompanyRulesCommonSOPO
from odoo.tests import Form
from odoo.tests import tagged


@tagged('post_install', '-at_install')
class TestInterCompanyPurchaseToSale(TestInterCompanyRulesCommonSOPO):

    def generate_purchase_order(self, company, partner):
        """ Generate purchase order and confirm its state """
        purchase_order = Form(self.env['purchase.order'])
        purchase_order.partner_id = partner
        purchase_order.company_id = company
        purchase_order.currency_id = company.currency_id
        purchase_order = purchase_order.save()

        with Form(purchase_order) as po:
            with po.order_line.new() as line:
                line.name = 'Service'
                line.product_id = self.product_consultant
                line.price_unit = 450.0

        # Confirm Purchase order
        purchase_order.button_confirm()
        # Check purchase order state should be purchase.
        self.assertEqual(purchase_order.state, 'purchase', 'Purchase order should be in purchase state.')
        return purchase_order

    def validate_generated_sale_order(self, purchase_order, company, partner):
        """ Validate sale order which has been generated from purchase order
        and test its state, total_amount, product_name and product_quantity.
        """

        # Find related sale order based on client order reference.
        sale_order = self.env['sale.order'].with_company(partner).search([('client_order_ref', '=', purchase_order.name)], limit=1)
        fp = self.env['account.fiscal.position'].with_company(partner)._get_fiscal_position(company.partner_id)

        self.assertEqual(sale_order.state, "draft", "sale order should be in draft state.")
        self.assertEqual(sale_order.partner_id, company.partner_id, "Vendor does not correspond to Company %s." % company)
        self.assertEqual(sale_order.company_id, partner, "Applied company in created sale order is incorrect.")
        self.assertEqual(sale_order.amount_total, 517.5, "Total amount is incorrect.")
        self.assertEqual(sale_order.order_line[0].product_id, self.product_consultant, "Product in line is incorrect.")
        self.assertEqual(sale_order.order_line[0].name, 'Service', "Product name is incorrect.")
        self.assertEqual(sale_order.order_line[0].product_uom_qty, 1, "Product qty is incorrect.")
        self.assertEqual(sale_order.order_line[0].price_unit, 450, "Unit Price in line is incorrect.")
        self.assertTrue(sale_order.partner_shipping_id == purchase_order.picking_type_id.warehouse_id.partner_id, "Partner shipping is incorrect.")
        self.assertEqual(sale_order.fiscal_position_id, fp)


    def test_00_inter_company_sale_purchase(self):
        """ Configure "Sale/Purchase" option and then Create purchase order and find related
        sale order to related company and compare them.
        """

        # Generate purchase order in company A for company B
        self.company_b.update({
            'rule_type': 'sale_purchase',
        })
        purchase_order = self.generate_purchase_order(self.company_a, self.company_b.partner_id)
        # Check sale order is created in company B ( for company A )
        self.validate_generated_sale_order(purchase_order, self.company_a, self.company_b)
        # reset configuration of company B
        self.company_b.update({
            'rule_type': False,
        })

        # Generate purchase order in company B for company A
        self.company_a.update({
            'rule_type': 'sale_purchase',
        })
        purchase_order = self.generate_purchase_order(self.company_b, self.company_a.partner_id)
        # Check sale order is created in company A ( for company B )
        self.validate_generated_sale_order(purchase_order, self.company_b, self.company_a)
        # reset configuration of company A
        self.company_a.update({
            'rule_type': False,
        })

    def test_01_inter_company_purchase_order_with_configuration(self):
        """ Configure only "purchase" option and then Create purchase order and find related
        sale order to related company and compare them.
        """

        # Generate purchase order in company A for company B
        self.company_b.update({
            'rule_type': 'purchase',
        })
        purchase_order = self.generate_purchase_order(self.company_a, self.company_b.partner_id)
        # Check sale order is created in company B ( for company A )
        self.validate_generated_sale_order(purchase_order, self.company_a, self.company_b)
        # reset configuration of company B
        self.company_b.update({
            'rule_type': False,
        })

        # Generate purchase order in company B for company A
        self.company_a.update({
            'rule_type': 'purchase',
        })
        purchase_order = self.generate_purchase_order(self.company_b, self.company_a.partner_id)
        # Check sale order is created in company A ( for company B )
        self.validate_generated_sale_order(purchase_order, self.company_b, self.company_a)
        # reset configuration  of company A
        self.company_a.update({
            'rule_type': False,
        })

    def test_02_inter_company_purchase_order_without_configuration(self):
        """ Without any Configuration Create purchase order and try to find related
        sale order to related company.
        """

        # without any inter_company configuration generate purchase_order in company A for company B
        purchase_order = self.generate_purchase_order(self.company_a, self.company_b.partner_id)
        # Find related sale order based on client order reference.
        sale_order = self.env['sale.order'].search([('client_order_ref', '=', purchase_order.name)], limit=1)
        self.assertTrue((not sale_order), "Sale order created for company B from Purchase order of company A without configuration")

        # without any inter_company configuration generate purchase_order in company B for company A
        purchase_order = self.generate_purchase_order(self.company_b, self.company_a.partner_id)
        # Find related sale order based on client order reference.
        sale_order = self.env['sale.order'].search([('client_order_ref', '=', purchase_order.name)], limit=1)
        self.assertTrue((not sale_order), "Sale order created for company A from Purchase order of company B without configuration")

    def test_03_inter_company_sale_purchase_auto_validation(self):
        today = fields.Datetime.today()
        (self.company_b | self.company_a).update({
            'rule_type': 'sale_purchase',
            'auto_validation': True
        })
        # Set parent relation
        self.company_b.parent_id = self.company_a.id
        supplier = self.env['res.partner'].create({
            'name': 'Blabli car',
            'company_id': False
        })

        mto_route = self.env['stock.route'].with_context(active_test=False).search([('name', '=', 'Replenish on Order (MTO)')])
        buy_route = self.env['stock.route'].search([('name', '=', 'Buy')])
        mto_route.active = True

        product_storable = self.env['product.product'].create({
            'name': 'Storable',
            'categ_id': self.env.ref('product.product_category_all').id,
            'type': 'product',
            'taxes_id': [(6, 0, (self.company_a.account_sale_tax_id + self.company_b.account_sale_tax_id).ids)],
            'supplier_taxes_id': [(6, 0, (self.company_a.account_purchase_tax_id + self.company_b.account_purchase_tax_id).ids)],
            'route_ids': [(6, 0, [buy_route.id, mto_route.id])],
            'company_id': False,
            'seller_ids': [
                (0, 0, {
                    'partner_id': self.company_a.partner_id.id,
                    'min_qty': 1,
                    'price': 250,
                    'company_id': self.company_b.id,
                }),
                (0, 0, {
                    'partner_id': supplier.id,
                    'min_qty': 1,
                    'price': 200,
                    'company_id': self.company_a.id,
                })
            ]
        })

        purchase_order = Form(self.env['purchase.order'].with_company(self.company_b))
        purchase_order.partner_id = self.company_a.partner_id
        purchase_order.company_id = self.company_b
        purchase_order.currency_id = self.company_b.currency_id
        purchase_order = purchase_order.save()

        with Form(purchase_order.with_company(self.company_b)) as po:
            with po.order_line.new() as line:
                line.product_id = product_storable

        purchase_order.date_planned = today + relativedelta(days=7)
        # Confirm Purchase order
        purchase_order.with_company(self.company_b).button_confirm()
        # Check purchase order state should be purchase.
        self.assertEqual(purchase_order.state, 'purchase', 'Purchase order should be in purchase state.')

        sale_order = self.env['sale.order'].with_company(self.company_a).search([
            ('client_order_ref', '=', purchase_order.name),
            ('company_id', '=', self.company_a.id)
        ], limit=1)
        self.assertTrue(sale_order)
        self.assertEqual(len(sale_order.order_line), 1)
        self.assertEqual(sale_order.order_line.product_id, product_storable)
        self.assertEqual(sale_order.commitment_date, today + relativedelta(days=7))
        # Check the MTO purchase, the seller should be the correct one
        po = self.env['purchase.order'].with_company(self.company_a).search([
            ('company_id', '=', self.company_a.id)
        ], limit=1, order='id DESC')
        self.assertTrue(po)
        self.assertEqual(po.partner_id, supplier)
        self.assertEqual(po.order_line.product_id, product_storable)
        self.assertEqual(po.order_line.price_unit, 200)


    def test_04_inter_company_purchase_order_with_stock_picking(self):
        partner = self.env['res.partner'].create({
            'name': 'Odoo',
            'child_ids': [
                (0, 0, {'name': 'Farm 1', 'type': 'delivery'}),
                (0, 0, {'name': 'Farm 2', 'type': 'delivery'}),
                (0, 0, {'name': 'Farm 3', 'type': 'delivery'}),
            ]
        })
        self.company_b.update({'partner_id': partner.id})
        (self.company_b | self.company_a).update({'rule_type': 'sale_purchase'})
        children = partner.child_ids
        warehouses = self.env['stock.warehouse'].sudo().create([
            {
                'name': 'Farm 1 warehouse',
                'code': 'FWH1',
                'company_id': self.company_b.id,
                'partner_id': children[0].id,
            },
            {
                'name': 'Farm 2 warehouse',
                'code': 'FWH2',
                'company_id': self.company_b.id,
                'partner_id': children[1].id,
            },
            {
                'name': 'Farm 3 warehouse',
                'code': 'FWH3',
                'company_id': self.company_b.id,
                'partner_id': children[2].id,
            },
        ])
        def generate_purchase_and_validate_sale_order(first_company, second_company, warehouse_id):
            stock_picking_type = self.env['stock.picking.type'].search(['&', ('warehouse_id', '=', warehouse_id), ('name', '=', 'Receipts')])
            purchase_order = Form(self.env['purchase.order'])
            purchase_order.partner_id = second_company.partner_id
            purchase_order.company_id = first_company
            purchase_order.currency_id = second_company.currency_id
            purchase_order = purchase_order.save()
            purchase_order.picking_type_id = stock_picking_type
            with Form(purchase_order) as po:
                with po.order_line.new() as line:
                    line.name = 'Service'
                    line.product_id = self.product_consultant
                    line.price_unit = 450.0
            purchase_order.with_company(first_company).button_confirm()
            self.validate_generated_sale_order(purchase_order, first_company, second_company)

        for warehouse in warehouses:
            generate_purchase_and_validate_sale_order(self.company_b, self.company_a, warehouse.id)

    def test_05_inter_company_purchase_order_from_so_with_custom_attribute_values(self):
        """
        Check that the custom attribute values are transfered by procurements
        """
        (self.company_b | self.company_a).update({'rule_type': 'sale_purchase'})
        self.company_a.warehouse_id.write({'delivery_steps': 'pick_pack_ship'})
        mto_route = self.env['stock.route'].with_context(active_test=False).search([('name', '=', 'Replenish on Order (MTO)')])
        buy_route = self.env['stock.route'].search([('name', '=', 'Buy')])
        mto_route.active = True

        # setup product with customizable attribute value
        product_storable = self.env['product.product'].create({
            'name': 'Storable',
            'type': 'product',
            'route_ids': [Command.set([buy_route.id, mto_route.id])],
            'company_id': False,
            'seller_ids': [
                Command.create({
                    'partner_id': self.company_b.partner_id.id,
                    'min_qty': 1,
                    'price': 250,
                    'company_id': self.company_a.id,
                }),
            ]
        })
        product_attribute = self.env['product.attribute'].create({
            'name': 'product attribute',
            'display_type': 'radio',
            'create_variant': 'always'
        })
        product_attribute_value = self.env['product.attribute.value'].create({
            'name': 'single product attribute value',
            'is_custom': True,
            'attribute_id': product_attribute.id
        })
        product_attribute_line = self.env['product.template.attribute.line'].create({
            'attribute_id': product_attribute.id,
            'product_tmpl_id': product_storable.product_tmpl_id.id,
            'value_ids': [Command.link(product_attribute_value.id)]
        })

        custom_value = "test"

        # create and confirm SO with comp a for a customer
        sale_order = self.env['sale.order'].with_company(self.company_a).create({
            'partner_id': self.partner_a.id,
            'order_line': [
                Command.create({
                    'name': 'COMP1 SO',
                    'product_id': product_storable.id,
                    'product_uom_qty': 1,
                    'product_custom_attribute_value_ids': [
                        Command.create({
                            'custom_product_template_attribute_value_id': product_attribute_line.product_template_value_ids.id,
                            'custom_value': custom_value,
                        })
                    ],
                })
            ],
        })
        sale_order.action_confirm()
        po = self.env['purchase.order'].search([('partner_id', '=', self.company_b.partner_id.id)])
        self.assertTrue(custom_value in po.order_line.display_name)
        po.with_company(self.company_a).button_confirm()
        auto_generated_so = self.env['sale.order'].search([('partner_id', '=', self.company_a.partner_id.id)])
        self.assertRecordValues(auto_generated_so, [{'auto_generated': True, 'auto_purchase_order_id': po.id}])
        self.assertTrue(custom_value in auto_generated_so.order_line.name)
        original_custom_attribute = sale_order.order_line.product_custom_attribute_value_ids
        copied_custom_attribute = auto_generated_so.order_line.product_custom_attribute_value_ids
        self.assertEqual(
            original_custom_attribute.custom_value,
            copied_custom_attribute.custom_value,
        )
        self.assertEqual(
            original_custom_attribute.custom_product_template_attribute_value_id,
            copied_custom_attribute.custom_product_template_attribute_value_id,
        )

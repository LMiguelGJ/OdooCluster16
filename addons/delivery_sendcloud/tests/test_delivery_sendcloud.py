import json
from contextlib import contextmanager
from unittest.mock import patch, DEFAULT
import requests

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from odoo import Command

@contextmanager
def _mock_sendcloud_call(warehouse_id):
    def _mock_request(*args, **kwargs):
        method = kwargs.get('method') or args[0]
        url = kwargs.get('url') or args[1]
        responses = {
            'get': {
                'shipping_methods': {'shipping_methods': [{'id': 8, 'name': 'test letter', 'min_weight': 0, 'max_weight': 20}]},
                'shipping-price': [{'price': '5.5', 'currency': 'EUR'}],
                'addresses': {'sender_addresses': [{'contact_name': warehouse_id.name, 'id': 1}]},
                'label': 'mock',
            },
            'post': {
                'parcels': {
                    'parcels': [{
                        'tracking_number': '123',
                        'tracking_url': 'url',
                        'id': 1, 'weight': 10.0,
                        'shipment': {'id': 8},
                        'documents': [{'link': '/label', 'type': 'label'}]
                    }],
                    'status': 'deleted'
                },
            }
        }

        for endpoint, content in responses[method].items():
            if endpoint in url:
                response = requests.Response()
                response._content = json.dumps(content).encode()
                response.status_code = 200
                return response

        raise Exception('unhandled request url %s' % url)

    with patch.object(requests.Session, 'request', _mock_request):
        yield

@tagged('-standard', 'external')
class TestDeliverySendCloud(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.your_company = cls.env.ref("base.main_partner")
        cls.warehouse_id = cls.env['stock.warehouse'].search([('company_id', '=', cls.your_company.id)], limit=1)
        cls.your_company.write({'name': 'Odoo SA',
                                'country_id': cls.env.ref('base.be').id,
                                'street': 'Chaussée de Namur 40',
                                'street2': False,
                                'state_id': False,
                                'city': 'Ramillies',
                                'zip': 1367,
                                'phone': '081813700',
                                })
        # deco_art will be in europe
        cls.eu_partner = cls.env.ref('base.res_partner_2')
        cls.eu_partner.write({
            'country_id': cls.env.ref('base.nl').id,
            'zip': '1105AA',
            'state_id': False
        })
        # partner in us (azure)
        cls.us_partner = cls.env.ref('base.res_partner_12')

        cls.product_to_ship1 = cls.env["product.product"].create({
            'name': 'Door with wings',
            'type': 'consu',
            'weight': 10.0
        })

        cls.product_to_ship2 = cls.env["product.product"].create({
            'name': 'Door with Legs',
            'type': 'consu',
            'weight': 15.0
        })

        shipping_product = cls.env['product.product'].create({
            'name': 'SendCloud Delivery',
            'type': 'service'
        })

        cls.sendcloud = cls.env['delivery.carrier'].create({
            'delivery_type': 'sendcloud',
            'product_id': shipping_product.id,
            'sendcloud_public_key': 'mock_key',
            'sendcloud_secret_key': 'mock_key',
            'name': 'SendCloud'
        })
        with _mock_sendcloud_call(cls.warehouse_id):
            wiz_action = cls.sendcloud.action_load_sendcloud_shipping_products()
            wiz = cls.env[wiz_action['res_model']].with_context(wiz_action['context']).create({
                'shipping_product': next(iter(wiz_action['context']['shipping_products'].keys()), False),  # choose first of shipping methods found
                'carrier_id': cls.sendcloud.id
            })
            wiz.action_validate()

    def test_deliver_inside_eu(self):
        sale_order = self.env['sale.order'].create({
            'partner_id': self.eu_partner.id,
            'order_line': [
                Command.create({
                    'product_id': self.product_to_ship1.id
                }),
                Command.create({
                    'product_id': self.product_to_ship2.id
                })
            ]
        })
        wiz_action = sale_order.action_open_delivery_wizard()
        choose_delivery_carrier = self.env[wiz_action['res_model']].with_context(wiz_action['context']).create({
            'carrier_id': self.sendcloud.id,
            'order_id': sale_order.id
        })
        with _mock_sendcloud_call(self.warehouse_id):
            # dont assert price since unstamped letter has a price of 0
            choose_delivery_carrier.update_price()
            choose_delivery_carrier.button_confirm()
            sale_order.action_confirm()
            self.assertGreater(len(sale_order.picking_ids), 0, "The Sales Order did not generate pickings for shipment.")

            picking = sale_order.picking_ids[0]
            self.assertEqual(picking.carrier_id.id, sale_order.carrier_id.id, "Carrier is not the same on Picking and on SO.")
            picking.action_assign()
            picking.action_set_quantities_to_reservation()
            self.assertGreater(picking.weight, 0.0, "Picking weight should be positive.")

            picking._action_done()
            self.assertIsNot(picking.sendcloud_parcel_ref, False,
                             "SendCloud Parcel Id not set")

    def test_deliver_outside_eu(self):
        '''
            Same workflow as inside EU but tests other inner workings of sendcloud service
        '''
        sale_order = self.env['sale.order'].create({
            'partner_id': self.us_partner.id,
            'order_line': [
                Command.create({
                    'product_id': self.product_to_ship1.id
                }),
                Command.create({
                    'product_id': self.product_to_ship2.id
                })
            ]
        })
        wiz_action = sale_order.action_open_delivery_wizard()
        choose_delivery_carrier = self.env[wiz_action['res_model']].with_context(wiz_action['context']).create({
            'carrier_id': self.sendcloud.id,
            'order_id': sale_order.id
        })
        with _mock_sendcloud_call(self.warehouse_id):
            # dont assert price since unstamped letter has a price of 0
            choose_delivery_carrier.update_price()
            choose_delivery_carrier.button_confirm()
            sale_order.action_confirm()
            self.assertGreater(len(sale_order.picking_ids), 0, "The Sales Order did not generate pickings for ups shipment.")

            picking = sale_order.picking_ids[0]
            self.assertEqual(picking.carrier_id.id, sale_order.carrier_id.id, "Carrier is not the same on Picking and on SO.")
            picking.action_assign()
            picking.action_set_quantities_to_reservation()
            self.assertGreater(picking.weight, 0.0, "Picking weight should be positive.")

            picking._action_done()
            self.assertIsNot(picking.sendcloud_parcel_ref, False,
                             "SendCloud Parcel Id not set")

    def test_extract_house_number(self):
        api = self.sendcloud._get_sendcloud()
        addresses = [
            ('Friedrichstr. 13/1', '13/1'),
            ('Rue du pont 11 A', '11 A'),
            ('Place Albert 1er 15B', '15B'),
            ('123-456 Main Street', '123-456'),
            ('456B Elm St', '456B'),
            ('789 C Oak Avenue', '789 C'),
            ('20A1 Vo Thi Sau Street, Tan Dinh Ward, District 1', '20A1')
        ]
        for address in addresses:
            self.assertEqual(api._get_house_number(address[0]), address[1])

    def test_sendcloud_picking_batch_validation(self):
        """
        Create 2 delivery orders with sendcloud carrier. Make them respectively
        valid and invalid on the carrier side. Validate the pickings in batch
        Since the pickings are processed unbatched on the carrier side the
        "UserError" of the invalid picking can not be raised and should be
        replaced by a warning activity.
        """
        alien = self.env['res.users'].create({
            'login': 'Mars Man',
            'name': 'Spleton',
            'email': 'alien@mars.com',
        })
        sale_orders = self.env['sale.order'].create([
            {
                'partner_id': self.eu_partner.id,
                'order_line': [
                    Command.create({
                        'product_id': self.product_to_ship1.id
                    }),
                ]
            },
            {
                'partner_id': self.eu_partner.id,
                'order_line': [
                    Command.create({
                        'product_id': self.product_to_ship2.id
                    }),
                ]
            },
        ])
        choose_delivery_carriers = [self.env[wiz_action['res_model']].with_context(wiz_action['context']).create({
            'carrier_id': self.sendcloud.id,
            'order_id': sale_orders[index].id
        }) for index, wiz_action in enumerate(sale_orders.mapped(lambda so: so.action_open_delivery_wizard()))]

        def fail_send_shipment(pick):
            # side effect to throw an error for a given picking but resolve the normal call for the other
            def _throw_error_on_chosen_picking(*args, **kwargs):
                if args and args[0] == pick:
                    raise UserError("Something went wrong, parcel not returned from Sendcloud: {'weight': ['The weight must be less than 10.001 kg']}")
                else:
                    return DEFAULT
            return _throw_error_on_chosen_picking

        with _mock_sendcloud_call(self.warehouse_id):
            for i in range(0, len(sale_orders)):
                choose_delivery_carriers[i].update_price()
                choose_delivery_carriers[i].button_confirm()
                sale_orders[i].action_confirm()
                # check that a delivery was created for the associated carrier
                self.assertEqual(sale_orders[i].picking_ids.carrier_id.id, sale_orders[i].carrier_id.id)
            pickings = sale_orders.picking_ids
            pickings.action_assign()
            pickings.action_set_quantities_to_reservation()
            sendcloud_class = 'odoo.addons.delivery_sendcloud.models.sendcloud_service.SendCloud'
            with patch(sendcloud_class + '.send_shipment', side_effect=fail_send_shipment(pickings[1])):
                pickings.with_user(alien).button_validate()
        # both pickings should be validated but and activity should have been created for the invalid picking
        self.assertEqual(pickings.mapped('state'), ['done', 'done'])
        self.assertTrue(self.env['mail.activity'].search([('res_model', '=', 'stock.picking'), ('res_id', '=', pickings[1].id), ('user_id', '=', alien.id)], limit=1))

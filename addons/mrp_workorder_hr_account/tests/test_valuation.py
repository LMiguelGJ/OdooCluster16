# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import Command
from odoo.addons.mrp_account.tests.test_valuation_layers import TestMrpValuationCommon
from odoo.tests import Form

from datetime import datetime, timedelta


class TestMrpWorkorderHrValuation(TestMrpValuationCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.employee = cls.env['hr.employee'].create({
            'name': 'Jean Michel',
            'hourly_cost': 100,
        })

        cls.employee_center = cls.env['mrp.workcenter'].create({
            'name': 'Jean Michel\'s Center',
            'costs_hour': 10,
            'allow_employee': True,
            'employee_ids': [(4, cls.employee.id)],
        })

        cls.bom.operation_ids = [(0, 0, {
            'name': 'Super Operation',
            'workcenter_id': cls.employee_center.id,
            'time_mode': 'manual',
            'time_cycle_manual': 60,
        })]

    def test_svl_includes_employee_cost(self):
        self.product1.categ_id.property_cost_method = 'fifo'

        mo_form = Form(self.env['mrp.production'])
        mo_form.bom_id = self.bom
        mo = mo_form.save()
        mo.action_confirm()

        with Form(mo) as mo_form:
            mo_form.qty_producing = 1

        # Register a productivity of one hour
        now = datetime.now()
        workorder = mo.workorder_ids
        self.env['mrp.workcenter.productivity'].create({
            'employee_id': self.employee.id,
            'workcenter_id': self.employee_center.id,
            'workorder_id': workorder.id,
            'date_start': now,
            'date_end': now + timedelta(hours=1),
            'loss_id': self.env.ref('mrp.block_reason7').id,
        })
        workorder.button_done()

        mo.button_mark_done()

        self.assertEqual(self.product1.stock_valuation_layer_ids.remaining_value, 110, 'Workcenter cost (10) + Employee cost (100)')

    def test_deletion_of_time_tracking(self):
        loss_id = self.env['mrp.workcenter.productivity.loss'].search([('loss_type', '=', 'productive')], limit=1)
        production_form = Form(self.env["mrp.production"])
        production_form.bom_id = self.bom
        production_form.product_qty = 10
        mo = production_form.save()
        mo.workorder_ids.write({
            'time_ids': [
                Command.create({
                    'user_id': self.env.user.id,
                    'date_start': datetime.now(),
                    'date_end': datetime.now() + timedelta(hours=1),
                    'workcenter_id': self.employee_center.id,
                    'loss_id': loss_id.id,
                }),
                Command.create({
                    'user_id': self.env.user.id,
                    'date_start': datetime.now() - timedelta(hours=1),
                    'date_end': datetime.now(),
                    'workcenter_id': self.employee_center.id,
                    'loss_id': loss_id.id,
                }),
            ]
        })
        mo.action_confirm()
        mo.workorder_ids.button_start()
        mo.workorder_ids.button_finish()
        time_ids_len = len(mo.workorder_ids.time_ids)
        mo.workorder_ids.time_ids[0].unlink()
        self.assertEqual(len(mo.workorder_ids.time_ids), time_ids_len - 1)

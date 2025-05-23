# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import itertools
from odoo import api, fields, models, _
from odoo.tools import groupby


class HelpdeskTicket(models.Model):
    _inherit = 'helpdesk.ticket'

    product_id = fields.Many2one('product.product', string='Product', tracking=True,
        domain="[('sale_ok', '=', True), ('id', 'in', suitable_product_ids), '|', ('company_id', '=', False), ('company_id', '=', company_id)]",
        help="Product this ticket is about. If selected, only the sales orders, deliveries and invoices including this product will be visible.")
    suitable_product_ids = fields.Many2many('product.product', compute='_compute_suitable_product_ids')
    has_partner_picking = fields.Boolean(compute='_compute_suitable_product_ids')
    tracking = fields.Selection(related='product_id.tracking')
    lot_id = fields.Many2one('stock.lot', string='Lot/Serial Number', domain="[('product_id', '=', product_id)]", tracking=True)
    pickings_count = fields.Integer('Return Orders Count', compute="_compute_pickings_count")
    picking_ids = fields.Many2many('stock.picking', string="Return Orders", copy=False)

    @api.depends('partner_id')
    def _compute_suitable_product_ids(self):
        """
        Computes the suitable products for the given tickets based on the associated
        partner's sales orders and outgoing deliveries.
        """
        suitable_partners_ids_by_commercial_partner_id = {
            row['commercial_partner_id'][0]: row['ids']
            for row in self.env['res.partner']._read_group(
                domain=[('commercial_partner_id', 'in', self.commercial_partner_id.ids)],
                fields=['ids:array_agg(id)'],
                groupby=['commercial_partner_id'],
                lazy=False,
            )
        }
        for commercial_partner, tickets_list in groupby(self, lambda t: t.commercial_partner_id):
            tickets = self.env['helpdesk.ticket'].browse(ticket.id for ticket in tickets_list)
            suitable_partner_ids = suitable_partners_ids_by_commercial_partner_id.get(commercial_partner.id)
            if not suitable_partner_ids:
                tickets.suitable_product_ids = False
                tickets.has_partner_picking = False
                continue
            sale_data = self.env['sale.order.line']._read_group([
                ('product_id', '!=', False),
                ('state', '=', 'sale'),
                ('order_partner_id', 'in', suitable_partner_ids),
            ], ['order_partner_id', 'product_id:array_agg(product_id)'], ['order_partner_id'], lazy=False)
            order_data = {data['order_partner_id'][0]: data['product_id'] for data in sale_data}

            picking_data = self.env['stock.picking']._read_group([
                ('state', '=', 'done'),
                ('partner_id', 'in', suitable_partner_ids),
                ('picking_type_code', '=', 'outgoing'),
            ], ['ids:array_agg(id)', 'id'], ['partner_id'], lazy=False)

            picking_mapped_data = {data['partner_id'][0]: data['ids'] for data in picking_data}
            picking_ids = list(picking_mapped_data.values())
            outgoing_product = {}
            if picking_ids and picking_ids[0]:
                move_line_data = self.env['stock.move.line']._read_group([
                    ('state', '=', 'done'),
                    ('picking_id', 'in', picking_ids[0]),
                    ('picking_code', '=', 'outgoing'),
                ], ['product_id:array_agg(product_id)'], ['picking_id'], lazy=False)
                move_lines = {data['picking_id'][0]: data['product_id'] for data in move_line_data}
                if move_lines:
                    for partner_id, picks in picking_mapped_data.items():
                        product_lists = [move_lines[pick] for pick in picks if pick in move_lines]
                        outgoing_product.update({partner_id: list(itertools.chain(*product_lists))})
            partners_in_sale = dict(zip(order_data.keys(), self.env['res.partner'].browse(order_data.keys())))
            product_ids = {item for partner_id in suitable_partner_ids for item in order_data.get(partner_id, []) + outgoing_product.get(partner_id, [])}
            tickets.suitable_product_ids = [fields.Command.set(product_ids)]
            tickets.has_partner_picking = any((partner_id in outgoing_product) or (partner_id in partners_in_sale) for partner_id in suitable_partner_ids)

    @api.onchange('suitable_product_ids')
    def onchange_product_id(self):
        if self.product_id not in self.suitable_product_ids:
            self.product_id = False

    @api.depends('picking_ids')
    def _compute_pickings_count(self):
        for ticket in self:
            ticket.pickings_count = len(ticket.picking_ids)

    def write(self, vals):
        res = super().write(vals)
        if 'suitable_product_ids' in vals:
            self.filtered(lambda t: t.product_id not in t.suitable_product_ids).product_id = False
        return res

    def action_view_pickings(self):
        self.ensure_one()
        action = {
            'type': 'ir.actions.act_window',
            'name': _('Return Orders'),
            'res_model': 'stock.picking',
            'view_mode': 'tree,form',
            'domain': [('id', 'in', self.picking_ids.ids)],
            'context': dict(self._context, create=False, default_company_id=self.company_id.id)
        }
        if self.pickings_count == 1:
            action.update({
                'view_mode': 'form',
                'res_id': self.picking_ids.id
            })
        return action

# Part of Odoo. See LICENSE file for full copyright and licensing details.

from collections import defaultdict

from odoo import _, api, fields, models
from odoo.osv import expression
from odoo.addons.sale_timesheet_enterprise.models.sale import DEFAULT_INVOICED_TIMESHEET
from odoo.tools.misc import unquote


class HelpdeskTeam(models.Model):
    _inherit = 'helpdesk.team'

    project_id = fields.Many2one(domain="[('allow_timesheets', '=', True), ('company_id', '=', company_id), ('allow_billable', '=', use_helpdesk_sale_timesheet)]")

    def _create_project(self, name, allow_billable, other):
        new_values = dict(other, allow_billable=allow_billable)
        return super(HelpdeskTeam, self)._create_project(name, allow_billable, new_values)

    def write(self, vals):
        result = super(HelpdeskTeam, self).write(vals)
        if 'use_helpdesk_sale_timesheet' in vals and vals['use_helpdesk_sale_timesheet']:
            projects = self.filtered(lambda team: team.project_id).mapped('project_id')
            projects.write({'allow_billable': True, 'timesheet_product_id': projects._default_timesheet_product_id()})
        return result

class HelpdeskTicket(models.Model):
    _inherit = 'helpdesk.ticket'

    def _domain_sale_line_id(self):
        domain = expression.AND([
            self.env['sale.order.line']._sellable_lines_domain(),
            [
                ('company_id', '=', unquote('company_id')),
                ('is_service', '=', True),
                ('order_partner_id', 'child_of', unquote('commercial_partner_id')),
                ('is_expense', '=', False),
                ('state', 'in', ['sale', 'done'])],
            ]
        )
        return domain

    use_helpdesk_sale_timesheet = fields.Boolean('Reinvoicing Timesheet activated on Team', related='team_id.use_helpdesk_sale_timesheet', readonly=True)
    sale_order_id = fields.Many2one('sale.order', compute="_compute_helpdesk_sale_order", compute_sudo=True, store=True, readonly=False)
    sale_line_id = fields.Many2one(
        'sale.order.line', string="Sales Order Item", tracking=True,
        compute="_compute_sale_line_id", store=True, readonly=False,
        domain=lambda self: str(self._domain_sale_line_id()),
        help="Sales Order Item to which the time spent on this ticket will be added in order to be invoiced to your customer.\n"
             "By default the last prepaid sales order item that has time remaining will be selected.\n"
             "Remove the sales order item in order to make this ticket non-billable.\n"
             "You can also change or remove the sales order item of each timesheet entry individually.")
    project_sale_order_id = fields.Many2one('sale.order', string="Project's sale order", related='project_id.sale_order_id')
    remaining_hours_available = fields.Boolean(related="sale_line_id.remaining_hours_available")
    remaining_hours_so = fields.Float('Remaining Hours on SO', compute='_compute_remaining_hours_so', search='_search_remaining_hours_so')

    @api.depends('sale_line_id', 'timesheet_ids', 'timesheet_ids.unit_amount')
    def _compute_remaining_hours_so(self):
        # TODO This is not yet perfectly working as timesheet.so_line stick to its old value although changed
        #      in the task From View.
        timesheets = self.timesheet_ids.filtered(lambda t: t.helpdesk_ticket_id.sale_line_id in (t.so_line, t._origin.so_line) and t.so_line.remaining_hours_available)

        mapped_remaining_hours = {ticket._origin.id: ticket.sale_line_id and ticket.sale_line_id.remaining_hours or 0.0 for ticket in self}
        uom_hour = self.env.ref('uom.product_uom_hour')
        for timesheet in timesheets:
            delta = 0
            if timesheet._origin.so_line == timesheet.helpdesk_ticket_id.sale_line_id:
                delta += timesheet._origin.unit_amount
            if timesheet.so_line == timesheet.helpdesk_ticket_id.sale_line_id:
                delta -= timesheet.unit_amount
            if delta:
                mapped_remaining_hours[timesheet.helpdesk_ticket_id._origin.id] += timesheet.product_uom_id._compute_quantity(delta, uom_hour)

        for ticket in self:
            ticket.remaining_hours_so = mapped_remaining_hours[ticket._origin.id]

    @api.model
    def _search_remaining_hours_so(self, operator, value):
        return [('sale_line_id.remaining_hours', operator, value)]

    @api.depends('commercial_partner_id', 'use_helpdesk_sale_timesheet', 'project_id.pricing_type', 'project_id.sale_line_id')
    def _compute_sale_line_id(self):
        billable_tickets = self.filtered('use_helpdesk_sale_timesheet')
        (self - billable_tickets).update({
            'sale_line_id': False
        })
        for ticket in billable_tickets:
            if ticket.project_id and ticket.project_id.pricing_type != 'task_rate':
                ticket.sale_line_id = ticket.project_id.sale_line_id
            # Check sale_line_id and customer are coherent
            if ticket.sale_line_id.sudo().order_partner_id.commercial_partner_id != ticket.commercial_partner_id:
                ticket.sale_line_id = False
            if not ticket.sale_line_id:
                ticket.sale_line_id = ticket._get_last_sol_of_customer()

    def _get_last_sol_of_customer(self):
        # Get the last SOL made for the customer in the current task where we need to compute
        self.ensure_one()
        if not self.commercial_partner_id or not self.project_id.allow_billable or not self.use_helpdesk_sale_timesheet:
            return False
        domain = [('company_id', '=', self.company_id.id), ('is_service', '=', True), ('order_partner_id', 'child_of', self.commercial_partner_id.id), ('is_expense', '=', False), ('state', 'in', ['sale', 'done']), ('remaining_hours', '>', 0)]
        if self.project_id.pricing_type != 'task_rate' and self.project_sale_order_id and self.commercial_partner_id == self.project_id.partner_id.commercial_partner_id:
            domain.append(('order_id', '=?', self.project_sale_order_id.id))
        return self.env['sale.order.line'].search(domain, limit=1)

    def write(self, values):
        recompute_so_lines = None
        other_timesheets = None
        if 'timesheet_ids' in values and isinstance(values.get('timesheet_ids'), (tuple, list)):
            # Then, we check if the list contains tuples/lists like "(code=1, timesheet_id, vals)" and we extract timesheet_id if it is an update and 'so_line' in vals
            timesheet_ids = [command[1] for command in values.get('timesheet_ids') if isinstance(command, (list, tuple)) and command[0] == 1 and 'so_line' in command[2]]
            recompute_so_lines = self.timesheet_ids.filtered(lambda t: t.id in timesheet_ids).mapped('so_line')
            if not self.env.user.has_group('hr_timesheet.group_hr_timesheet_approver') and values.get('sale_line_id', None):
                # We need to search the timesheets of other employee to update the so_line
                other_timesheets = self.env['account.analytic.line'].sudo().search([('id', 'not in', timesheet_ids), ('helpdesk_ticket_id', '=', self.id)])

        res = super(HelpdeskTicket, self).write(values)
        if other_timesheets:
            # Then we update the so_line if needed
            compute_timesheets = defaultdict(list, [(timesheet, timesheet.so_line) for timesheet in other_timesheets])  # key = timesheet and value = so_line of the timesheet before the _compute_so_line
            other_timesheets._compute_so_line()
            for timesheet, sol in compute_timesheets.items():
                if timesheet.so_line != sol:
                    recompute_so_lines |= sol
        if recompute_so_lines:
            recompute_so_lines._compute_qty_delivered()
        return res

    @api.depends('sale_line_id', 'project_id.sale_order_id')
    def _compute_helpdesk_sale_order(self):
        for ticket in self:
            if ticket.sale_line_id:
                ticket.sale_order_id = ticket.sale_line_id.order_id
            elif ticket.project_id.sale_order_id:
                ticket.sale_order_id = ticket.project_id.sale_order_id
            else:
                ticket.sale_order_id = False
            if ticket.sale_order_id and not ticket.partner_id:
                ticket.partner_id = ticket.sale_order_id.partner_id

    @api.model
    def _sla_reset_trigger(self):
        field_list = super()._sla_reset_trigger()
        field_list.append('sale_line_id')
        return field_list

    def _sla_find_extra_domain(self):
        domain = super()._sla_find_extra_domain()
        return expression.OR([domain, [
            '|', ('sale_line_ids', 'in', self.sale_line_id.ids), ('sale_line_ids', '=', False),
        ]])

    def action_view_so(self):
        self.ensure_one()
        action_window = {
            "type": "ir.actions.act_window",
            "res_model": "sale.order",
            "name": _("Sales Order"),
            "views": [[False, "form"]],
            "context": {"create": False, "show_sale": True},
            "res_id": self.sale_line_id.order_id.id or self.sale_order_id.id
        }
        return action_window


class AccountAnalyticLine(models.Model):
    _inherit = 'account.analytic.line'

    display_sol = fields.Boolean(compute="_compute_display_sol")

    @api.depends('helpdesk_ticket_id')
    def _compute_commercial_partner(self):
        timesheets_with_ticket = self.filtered('helpdesk_ticket_id')
        super(AccountAnalyticLine, self - timesheets_with_ticket)._compute_commercial_partner()
        for line in timesheets_with_ticket:
            line.commercial_partner_id = line.helpdesk_ticket_id.commercial_partner_id

    @api.depends('helpdesk_ticket_id', 'helpdesk_ticket_id.use_helpdesk_sale_timesheet')
    def _compute_display_sol(self):
        sale_project_ids = list(self.env['project.project']._search([('helpdesk_team.use_helpdesk_sale_timesheet', '=', True)]))
        for line in self:
            if line.project_id and not line.project_id.allow_billable and line.project_id.id not in sale_project_ids:
                line.display_sol = False
            else:
                line.display_sol = not line.helpdesk_ticket_id or line.helpdesk_ticket_id.use_helpdesk_sale_timesheet

    @api.depends('task_id.sale_line_id', 'project_id.sale_line_id', 'employee_id', 'project_id.allow_billable', 'helpdesk_ticket_id.sale_line_id')
    def _compute_so_line(self):
        non_billed_helpdesk_timesheets = self.filtered(lambda t: not t.is_so_line_edited and t.helpdesk_ticket_id and t._is_not_billed())
        for timesheet in non_billed_helpdesk_timesheets:
            timesheet.so_line = timesheet.project_id.allow_billable and timesheet.helpdesk_ticket_id.sale_line_id
        super(AccountAnalyticLine, self - non_billed_helpdesk_timesheets)._compute_so_line()

    @api.depends('timesheet_invoice_id.state')
    def _compute_partner_id(self):
        super(AccountAnalyticLine, self.filtered(lambda t: t._is_not_billed()))._compute_partner_id()

    def _get_portal_helpdesk_timesheet(self):
        param_invoiced_timesheet = self.env['ir.config_parameter'].sudo().get_param('sale.invoiced_timesheet', DEFAULT_INVOICED_TIMESHEET)
        if param_invoiced_timesheet == 'approved':
            return self.filtered(lambda line: line.validated)
        return self

    def _check_timesheet_can_be_billed(self):
        return super(AccountAnalyticLine, self)._check_timesheet_can_be_billed() or self.so_line == self.helpdesk_ticket_id.sale_line_id

    def _timesheet_get_sale_domain(self, order_lines_ids, invoice_ids):
        domain = super(AccountAnalyticLine, self)._timesheet_get_sale_domain(order_lines_ids, invoice_ids)
        if not invoice_ids:
            return domain

        return expression.OR([domain, [
            '&',
                '&',
                    ('task_id', '=', False),
                    ('helpdesk_ticket_id', '!=', False),
                '&',
                    ('so_line', 'in', order_lines_ids.ids),
                    ('timesheet_invoice_id', '=', False),
        ]])


class HelpdeskSLA(models.Model):
    _inherit = 'helpdesk.sla'

    sale_line_ids = fields.Many2many(
        'sale.order.line', string="Sales Order Items",
        domain="[('is_service', '=', True)]")

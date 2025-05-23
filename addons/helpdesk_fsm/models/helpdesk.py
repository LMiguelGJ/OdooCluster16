# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details
from ast import literal_eval

from odoo import models, api, fields, _


class HelpdeskTeam(models.Model):
    _inherit = 'helpdesk.team'

    fsm_project_id = fields.Many2one('project.project', string='FSM Project', domain=[('is_fsm', '=', True)],
        readonly=False, store=True, compute='_compute_fsm_project_id')

    @api.depends('use_fsm', 'company_id')
    def _compute_fsm_project_id(self):
        '''
        Compute the default fsm project from the same company
        as the helpdesk team when 'use_fsm' is enabled.
        '''
        fsm_teams_without_project = self.filtered(lambda t: t.use_fsm and not t.fsm_project_id)
        if fsm_teams_without_project:
            project_read_group = self.env['project.project'].read_group(
                domain=[
                    ('is_fsm', '=', True),
                    ('company_id', 'in', fsm_teams_without_project.company_id.ids),
                ],
                fields=['ids:array_agg(id)'],
                groupby=['company_id'],
            )
            mapped_project_per_company = {res['company_id'][0]: self.env['project.project'].browse(min(res['ids'])) for res in project_read_group}
        for team in fsm_teams_without_project:
            team.fsm_project_id = mapped_project_per_company.get(team.company_id.id, False)
        (self - fsm_teams_without_project).fsm_project_id = False

    # ---------------------------------------------------
    # Mail gateway
    # ---------------------------------------------------

    def _mail_get_message_subtypes(self):
        res = super()._mail_get_message_subtypes()
        if len(self) == 1:
            task_done_subtype = self.env.ref('helpdesk_fsm.mt_team_ticket_task_done')
            if not self.use_fsm and task_done_subtype in res:
                res -= task_done_subtype
        return res

class HelpdeskTicket(models.Model):
    _inherit = 'helpdesk.ticket'

    use_fsm = fields.Boolean(related='team_id.use_fsm')
    fsm_task_ids = fields.One2many('project.task', 'helpdesk_ticket_id', string='Tasks', help='Tasks generated from this ticket', domain=[('is_fsm', '=', True)], copy=False)
    fsm_task_count = fields.Integer(compute='_compute_fsm_task_count')

    @api.depends('fsm_task_ids')
    def _compute_fsm_task_count(self):
        ticket_groups = self.env['project.task'].read_group([('is_fsm', '=', True), ('helpdesk_ticket_id', '!=', False)], ['id:count_distinct'], ['helpdesk_ticket_id'])
        ticket_count_mapping = dict(map(lambda group: (group['helpdesk_ticket_id'][0], group['helpdesk_ticket_id_count']), ticket_groups))
        for ticket in self:
            ticket.fsm_task_count = ticket_count_mapping.get(ticket.id, 0)

    def action_view_fsm_tasks(self):
        action = self.env['ir.actions.act_window']._for_xml_id('industry_fsm.project_task_action_all_fsm')
        action['context'] = dict(literal_eval(action.get('context', '{}')), create=False)

        if len(self.fsm_task_ids) == 1:
            fsm_form_view = self.env.ref('project.view_task_form2')
            action.update(res_id=self.fsm_task_ids[0].id, views=[(fsm_form_view.id, 'form')])
        else:
            action.update(domain=[('id', 'in', self.fsm_task_ids.ids)], name=_('Tasks'))
        return action

    def action_generate_fsm_task(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Create a Field Service task'),
            'res_model': 'helpdesk.create.fsm.task',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'use_fsm': True,
                'default_helpdesk_ticket_id': self.id,
                'default_user_id': False,
                'default_partner_id': self.partner_id.id if self.partner_id else False,
                'default_name': self.name,
                'default_project_id': self.team_id.sudo().fsm_project_id.id,
            }
        }

    # ---------------------------------------------------
    # Mail gateway
    # ---------------------------------------------------

    def _mail_get_message_subtypes(self):
        res = super()._mail_get_message_subtypes()
        if len(self) == 1 and self.team_id:
            task_done_subtype = self.env.ref('helpdesk_fsm.mt_ticket_task_done')
            if not self.team_id.use_fsm and task_done_subtype in res:
                res -= task_done_subtype
        return res

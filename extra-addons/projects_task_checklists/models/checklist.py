# -*- coding: utf-8 -*-
##############################################################################
#
#    Cybrosys Technologies Pvt. Ltd.
#
#    Copyright (C) 2022-TODAY Cybrosys Technologies(<https://www.cybrosys.com>).
#    you can modify it under the terms of the GNU LESSER
#    GENERAL PUBLIC LICENSE (LGPL v3), Version 3.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU LESSER GENERAL PUBLIC LICENSE (LGPL v3) for more details.
#
#    You should have received a copy of the GNU LESSER GENERAL PUBLIC LICENSE
#    GENERAL PUBLIC LICENSE (LGPL v3) along with this program.
#    If not, see <https://www.gnu.org/licenses/>.
#
##############################################################################

from odoo import models, fields, api, _


class TaskChecklist(models.Model):
    _name = 'task.checklist'

    name = fields.Char(string='Name')
    description = fields.Char(string='Description')
    task_ids = fields.Many2one('project.task', string='Task')
    project_id = fields.Many2one('project.project', string='Project')

    checklist_ids = fields.One2many('checklist.item', 'checklist_id', string='CheckList Items', required=True)


class CheckListItem(models.Model):
    _name = 'checklist.item'
    _description = "Checklist Item"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=1)
    description = fields.Char()
    checklist_id = fields.Many2one('task.checklist')


class ChecklistItemLine(models.Model):
    _name = 'checklist.item.line'
    _description = 'Checklist Item Line'

    check_list_item_id = fields.Many2one('checklist.item', required=True)
    description = fields.Char()
    projects_id = fields.Many2one('project.task')
    checklist_id = fields.Many2one('task.checklist')
    state = fields.Selection(string='Status', required=True, readonly=True, copy=False, tracking=True, selection=[
        ('todo', 'To Do'),
        ('in_progress', 'In Progress'),
        ('done', 'Done'),
        ('cancel', 'Cancelled'),
    ], default='todo', )

    def approve_and_next(self):
        self.state = 'in_progress'

    def mark_completed(self):
        self.state = 'done'

    def mark_canceled(self):
        self.state = 'cancel'

    def reset_stage(self):
        self.state = 'todo'

    @api.model
    def create(self, vals):
        # Get the project task related to this checklist item line
        project_task = self.env['project.task'].browse(vals.get('projects_id'))
        
        # Find the checklist item with the same name as the project task
        checklist_item = self.env['checklist.item'].search([('name', '=', project_task.name)], limit=1)
        
        # Set the check_list_item_id to the found checklist item
        if checklist_item:
            vals['check_list_item_id'] = checklist_item.id
        
        # Call the original create method to create the checklist item line
        return super(ChecklistItemLine, self).create(vals)


class ChecklistProgress(models.Model):
    _inherit = 'project.task'

    start_date = fields.Datetime(string='Start Date')
    end_date = fields.Datetime(string='End Date')
    checklist_progress = fields.Float(compute='_compute_checklist_progress', string='Progress in %')
    checklist_ids = fields.Many2many('task.checklist', compute='_compute_checklist_ids')
    checklist_id = fields.Many2one('task.checklist')
    checklists = fields.One2many('checklist.item.line', 'projects_id', string='CheckList Items', required=True)

    @api.onchange('checklist_id')
    def _onchange_project_id(self):
        checklist = self.env['task.checklist'].search([('name', '=', self.checklist_id.name)])
        self.checklists = False
        self.checklists = [(0, 0, {
            'check_list_item_id': rec.id,
            'state': 'todo',
            'checklist_id': self.checklist_id.id,
        }) for rec in checklist.checklist_ids]

    def _compute_checklist_ids(self):
        for rec in self:
            self.checklist_ids = self.env['task.checklist'].search([('task_ids', '=', rec.id)])

    def _compute_checklist_progress(self):
        for rec in self:
            total_completed = 0
            for activity in rec.checklists:
                if activity.state in ['cancel', 'done', 'in_progress']:
                    total_completed += 1
            if total_completed:
                rec.checklist_progress = float(total_completed) / len(rec.checklists) * 100

            else:
                rec.checklist_progress = 0.0

    @api.model
    def create(self, vals):
        # Call the original create method to create the task
        task = super(ChecklistProgress, self).create(vals)

        # Get the project_id from the task
        project_id = vals.get('project_id')

        if project_id:
            # Find the corresponding task.checklist
            checklist = self.env['task.checklist'].search([('project_id', '=', project_id)], limit=1)
            if checklist:
                # Set the checklist_id to the found checklist
                task.checklist_id = checklist.id

                # Create a new checklist.item using the task's name
                self.env['checklist.item'].create({
                    'name': task.name,
                    'checklist_id': checklist.id,
                })

        return task

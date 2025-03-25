from odoo import models, fields, api

class ProjectProject(models.Model):
    _inherit = 'project.project'

    @api.model
    def create(self, vals):
        # Llamar al método create original para crear el proyecto
        project = super(ProjectProject, self).create(vals)
        
        # Crear un task.checklist asociado al proyecto
        self.env['task.checklist'].create({
            'name': project.name,
            'project_id': project.id,
        })
        
        return project
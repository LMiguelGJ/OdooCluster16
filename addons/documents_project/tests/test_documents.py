# -*- coding: utf-8 -*-

import base64
from datetime import date, timedelta

from odoo import Command

from odoo.addons.project.tests.test_project_base import TestProjectCommon
from odoo.exceptions import AccessError, UserError


GIF = b"R0lGODdhAQABAIAAAP///////ywAAAAAAQABAAACAkQBADs="
TEXT = base64.b64encode(bytes("workflow bridge project", 'utf-8'))


class TestCaseDocumentsBridgeProject(TestProjectCommon):

    def setUp(self):
        super(TestCaseDocumentsBridgeProject, self).setUp()
        self.folder_a = self.env['documents.folder'].create({
            'name': 'folder A',
        })
        self.folder_a_a = self.env['documents.folder'].create({
            'name': 'folder A - A',
            'parent_folder_id': self.folder_a.id,
        })
        self.attachment_txt = self.env['documents.document'].create({
            'datas': TEXT,
            'name': 'file.txt',
            'mimetype': 'text/plain',
            'folder_id': self.folder_a_a.id,
        })
        self.workflow_rule_task = self.env['documents.workflow.rule'].create({
            'domain_folder_id': self.folder_a.id,
            'name': 'workflow rule create task on f_a',
            'create_model': 'project.task',
        })

    def test_bridge_folder_workflow(self):
        """
        tests the create new business model (project).

        """
        self.assertEqual(self.attachment_txt.res_model, 'documents.document', "failed at default res model")
        self.workflow_rule_task.apply_actions([self.attachment_txt.id])

        self.assertEqual(self.attachment_txt.res_model, 'project.task', "failed at workflow_bridge_documents_project"
                                                                        " new res_model")
        task = self.env['project.task'].search([('id', '=', self.attachment_txt.res_id)])
        self.assertTrue(task.exists(), 'failed at workflow_bridge_documents_project task')
        self.assertEqual(self.attachment_txt.res_id, task.id, "failed at workflow_bridge_documents_project res_id")

    def test_bridge_parent_folder(self):
        """
        Tests the "Parent Workspace" setting
        """
        parent_folder = self.env.ref('documents_project.documents_project_folder')
        self.assertEqual(self.project_pigs.documents_folder_id.parent_folder_id, parent_folder, "The workspace of the project should be a child of the 'Projects' workspace.")

    def test_bridge_project_project_settings_on_write(self):
        """
        Makes sure the settings apply their values when an document is assigned a res_model, res_id
        """

        attachment_txt_test = self.env['ir.attachment'].create({
            'datas': TEXT,
            'name': 'fileText_test.txt',
            'mimetype': 'text/plain',
            'res_model': 'project.project',
            'res_id': self.project_pigs.id,
        })
        attachment_gif_test = self.env['ir.attachment'].create({
            'datas': GIF,
            'name': 'fileText_test.txt',
            'mimetype': 'text/plain',
            'res_model': 'project.task',
            'res_id': self.task_1.id,
        })

        txt_doc = self.env['documents.document'].search([('attachment_id', '=', attachment_txt_test.id)])
        gif_doc = self.env['documents.document'].search([('attachment_id', '=', attachment_gif_test.id)])

        self.assertEqual(txt_doc.folder_id, self.project_pigs.documents_folder_id, 'the text test document should have a folder')
        self.assertEqual(gif_doc.folder_id, self.project_pigs.documents_folder_id, 'the gif test document should have a folder')

    def test_bridge_document_is_shared(self):
        """
        Tests that the `is_shared` computed field on `documents.document` is working as intended.
        """
        self.assertFalse(self.attachment_txt.is_shared, "The document should not be shared by default")

        share_link = self.env['documents.share'].create({
            'folder_id': self.folder_a_a.id,
            'include_sub_folders': False,
            'type': 'domain',
        })
        self.folder_a_a._compute_is_shared()
        self.attachment_txt._compute_is_shared()

        self.assertTrue(self.attachment_txt.is_shared, "The document should be shared by a link sharing its folder")

        share_link.write({
            'folder_id': self.folder_a.id,
            'include_sub_folders': True,
        })
        self.folder_a_a._compute_is_shared()
        self.attachment_txt._compute_is_shared()

        self.assertTrue(self.attachment_txt.is_shared, "The document should be shared by a link sharing on of its ancestor folders with the subfolders option enabled")
        # We assume the rest of the cases depending on whether the document folder is shared are handled by the TestDocumentsFolder test in `documents`

        share_link.write({
            'include_sub_folders': False,
            'type': 'ids',
            'document_ids': [Command.link(self.attachment_txt.id)],
        })
        self.folder_a_a._compute_is_shared()
        self.attachment_txt._compute_is_shared()

        self.assertFalse(self.folder_a_a.is_shared, "The folder should not be shared")
        self.assertTrue(self.attachment_txt.is_shared, "The document should be shared by a link sharing it by id")

        share_link.write({'date_deadline': date.today() + timedelta(days=-1)})
        self.attachment_txt._compute_is_shared()

        self.assertFalse(self.attachment_txt.is_shared, "The document should be shared by an expired link sharing it by id")

        share_link.write({'date_deadline': date.today() + timedelta(days=1)})
        self.attachment_txt._compute_is_shared()

        self.assertTrue(self.attachment_txt.is_shared, "The document should be shared by a link sharing it by id and not expired yet")

    def test_copy_and_merge_folders(self):
        """
        Create 3 folders (folderA, folderB, folderC) with different properties (subfolders, tags, workflow actions)
        and merge them. The merged folder should have all the properties of the original folders combined.
        """
        folderA, folderB, folderC = self.env['documents.folder'].create([{
            'name': f'folder{l}',
        } for l in 'ABC'])

        folderA_child = self.env['documents.folder'].create({
            'name': 'folderA_child',
            'parent_folder_id': folderA.id,
        })
        folderB_facet = self.env['documents.facet'].create({
            'name': 'folderB_facet',
            'folder_id': folderB.id,
        })
        folderB_tag = self.env['documents.tag'].create({
            'name': 'folderB_tag',
            'facet_id': folderB_facet.id,
        })
        folderC_workflow_rule = self.env['documents.workflow.rule'].create({
            'name': 'folderC_workflow_rule',
            'domain_folder_id': folderC.id,
            'condition_type': 'criteria',
            'criteria_partner_id': self.partner_1.id,
        })
        self.env['documents.workflow.action'].create({
            'workflow_rule_id': folderC_workflow_rule.id,
            'action': 'remove',
        })

        copied_folder = (folderA + folderB + folderC)._copy_and_merge()

        self.assertEqual(len(copied_folder.children_folder_ids), 1)
        self.assertEqual(folderA_child.name, copied_folder.children_folder_ids[0].name)

        self.assertEqual(len(copied_folder.facet_ids), 1)
        facet_copy = copied_folder.facet_ids[0]
        self.assertEqual(folderB_facet.name, facet_copy.name)

        self.assertEqual(len(facet_copy.tag_ids), 1)
        self.assertEqual(folderB_tag.name, facet_copy.tag_ids[0].name)

        workflow_rule_copy_search = self.env['documents.workflow.rule'].search([('domain_folder_id', '=', copied_folder.id)])
        self.assertEqual(len(workflow_rule_copy_search), 1)
        workflow_rule_copy = workflow_rule_copy_search[0]
        self.assertEqual(folderC_workflow_rule.name, workflow_rule_copy.name)

        workflow_action_search = self.env['documents.workflow.action'].search([('workflow_rule_id', '=', workflow_rule_copy.id)])
        self.assertEqual(len(workflow_action_search), 1)

    def test_project_document_count(self):
        projects = self.project_pigs | self.project_goats
        self.assertEqual(self.project_pigs.document_count, 0)
        self.attachment_txt.write({
            'res_model': 'project.project',
            'res_id': self.project_pigs.id,
        })
        projects._compute_attached_document_count()
        self.assertEqual(self.project_pigs.document_count, 1, "The documents linked to the project should be taken into account.")
        self.env['documents.document'].create({
            'datas': GIF,
            'name': 'fileText_test.txt',
            'mimetype': 'text/plain',
            'folder_id': self.folder_a_a.id,
            'res_model': 'project.task',
            'res_id': self.task_1.id,
        })
        projects._compute_attached_document_count()
        self.assertEqual(self.project_pigs.document_count, 2, "The documents linked to the tasks of the project should be taken into account.")

    def test_upload_document_to_workspace_with_edit_shared(self):
        """
        A user that has Users access to both Documents and Projects, has been invited with an Editable Share link to the project,
        should be able to upload new documents to the project workspace folder
        """
        some_user = self.env['res.users'].create({
            'name': 'Some User',
            'login': 'some_user',
            'password': 'some_user',
            'groups_id': [(6, 0, [self.env.ref('documents.group_documents_user').id,
                                  self.env.ref('project.group_project_user').id])]
        })
        some_partner = self.env['res.partner'].create({
            'name': 'Some Partner',
            'user_id': some_user.id,
        })
        # create a folder for the project
        folder = self.env['documents.folder'].sudo().create({
            'name': 'folder_name',
        })
        self.project_pigs.sudo().write({
            'collaborator_ids': [(0, 0, {"partner_id": some_partner.id})],
            'collaborator_count': 1,
            'documents_folder_id': folder.id,
        })
        try:
            self.env['documents.document'].with_user(some_user).create({
                'datas': GIF,
                'name': 'fileText_test.txt',
                'mimetype': 'text/plain',
                'folder_id': folder.id,
                'res_model': 'project.project',
                'res_id': self.project_pigs.id,
            })
        except AccessError:
            self.fail("We got an access error, when we shouldn't have it, because we have edit access to the project (via shared link)")

    def test_project_task_access_document(self):
        """
        Tests that 'MissingRecord' error should not be rasied when trying to switch
        workspace for a non-existing document.

        - The 'active_id' here is the 'id' of a non-existing document.
        - We then try to access 'All' workspace by calling the 'search_panel_select_range'
            method. We should be able to access the workspace.
        """
        missing_id = self.env['documents.document'].search([], order='id DESC', limit=1).id + 1
        result = self.env['documents.document'].with_context(
            active_id=missing_id, active_model='project.task',
            limit_folders_to_project=True).search_panel_select_range('folder_id')
        self.assertTrue(result)

    def test_copy_project(self):
        """
        When duplicating a project, there should be exactly one copy of the folder linked to the project.
        If there is the `no_create_folder` context key, then the folder should not be copied (note that in normal flows,
        when this context key is used, it is expected that a folder will be copied/created manually, so that we don't
        end up with a project having the documents feature enabled but no folder).
        """
        last_folder_id = self.env['documents.folder'].search([], order='id desc', limit=1).id
        self.project_pigs.copy()
        self.assertEqual(len(self.env['documents.folder'].search([('id', '>', last_folder_id)])), 1, "There should only be one new folder created.")
        self.project_goats.with_context(no_create_folder=True).copy()
        self.assertEqual(len(self.env['documents.folder'].search([('id', '>', last_folder_id + 1)])), 0, "There should be no new folder created.")

    def test_delete_project_folder(self):
        """
        It should not be possible to delete the "Projects" folder.
        """
        project_folder = self.env.ref('documents_project.documents_project_folder')
        with self.assertRaises(UserError, msg="It should not be possible to delete the 'Projects' folder"):
            project_folder.unlink()

        current = project_folder
        for i in range(3):
            current.parent_folder_id = self.env['documents.folder'].create({
                "name": f"Ancestor Test {i}",
            })
            current = current.parent_folder_id

        with self.assertRaises(UserError, msg="It should not be possible to delete an ancestor of the 'Projects' folder"):
            current.unlink()

    def test_delete_folder_used_by_project(self):
        """
        It shouldn't be possible to delete a folder that is used by one or multiple projects.
        """
        project = self.env['project.project'].create({
            'name': "Test",
        })
        with self.assertRaises(UserError):
            project.documents_folder_id.unlink()
        project.use_documents = False
        project.documents_folder_id.unlink()

        project_1, project_2, project_3 = self.env['project.project'].create([{
            'name': f"Test Project {i}",
        } for i in range(3)])
        project_2.documents_folder_id = project_3.documents_folder_id
        (project_1 | project_2).documents_folder_id.parent_folder_id = parent_folder = self.env['documents.folder'].create({
            'name': "Test Folder",
        })
        with self.assertRaises(UserError, msg="It shouldn't be possible to delete a folder that is used by one or multiple projects."):
            parent_folder.unlink()

        (project_1 | project_3).use_documents = False
        with self.assertRaises(UserError, msg="It shouldn't be possible to delete a folder that is used by one or multiple projects."):
            parent_folder.unlink()

        project_2.use_documents = False
        # Should not raise an error
        parent_folder.unlink()

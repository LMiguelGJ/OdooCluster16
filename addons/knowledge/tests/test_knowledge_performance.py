# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo.addons.knowledge.tests.common import KnowledgeCommonWData
from odoo.tests.common import tagged, users, warmup
from odoo.tools import mute_logger


@tagged('knowledge_performance', 'post_install', '-at_install')
class KnowledgePerformanceCase(KnowledgeCommonWData):

    def setUp(self):
        super().setUp()
        # patch registry to simulate a ready environment
        self.patch(self.env.registry, 'ready', True)
        self._flush_tracking()

    def _flush_tracking(self):
        """ Force the creation of tracking values notably, and ensure tests are
        reproducible. """
        self.env.flush_all()
        self.cr.flush()

    @users('admin')
    @warmup
    def test_article_copy_batch(self):
        """ Test performance of batch-copying articles, which implies notably
        a descendants checks which might be costly.

        Done as admin as only admin has access to Duplicate button currently."""
        with self.assertQueryCount(admin=59):
            workspace_children = self.workspace_children.with_env(self.env)
            shared = self.article_shared.with_env(self.env)
            _duplicates = (workspace_children + shared).copy_batch()
            self.assertEqual(len(_duplicates), 3)

    @users('employee')
    @warmup
    def test_article_creation_single_shared_grandchild(self):
        """ Test with 2 levels of hierarchy in a private/shared environment """
        with self.assertQueryCount(employee=24):
            _article = self.env['knowledge.article'].create({
                'body': '<p>Hello</p>',
                'name': 'Article in shared',
                'parent_id': self.shared_children[0].id,
            })

        self.assertEqual(_article.category, 'shared')

    @users('employee')
    @warmup
    def test_article_creation_single_workspace(self):
        with self.assertQueryCount(employee=21):
            _article = self.env['knowledge.article'].create({
                'body': '<p>Hello</p>',
                'name': 'Article in workspace',
                'parent_id': self.article_workspace.id,
            })

        self.assertEqual(_article.category, 'workspace')

    @users('employee')
    @warmup
    def test_article_creation_multi_roots(self):
        with self.assertQueryCount(employee=23):
            _article = self.env['knowledge.article'].create([
                {'body': '<p>Hello</p>',
                 'internal_permission': 'write',
                 'name': f'Article {index} in workspace',
                }
                for index in range(10)
            ])

    @users('employee')
    @warmup
    def test_article_creation_multi_shared_grandchild(self):
        with self.assertQueryCount(employee=51):
            _article = self.env['knowledge.article'].create([
                {'body': '<p>Hello</p>',
                 'name': f'Article {index} in workspace',
                 'parent_id': self.shared_children[0].id,
                }
                for index in range(10)
            ])

    @users('employee')
    @warmup
    def test_article_favorite(self):
        with self.assertQueryCount(employee=16):
            shared_article = self.shared_children[0].with_env(self.env)
            shared_article.action_toggle_favorite()

    @users('employee')
    @warmup
    def test_article_get_valid_parent_options(self):
        with self.assertQueryCount(employee=12):  # knowledge: 12
            child_writable_article = self.workspace_children[1].with_env(self.env)
            # don't check actual results, those are tested in ``TestKnowledgeArticleUtilities`` class
            _res = child_writable_article.get_valid_parent_options(search_term="")

    @users('employee')
    @warmup
    def test_article_home_page(self):
        with self.assertQueryCount(employee=18):
            self.env['knowledge.article'].action_home_page()

    @mute_logger('odoo.addons.base.models.ir_rule', 'odoo.addons.mail.models.mail_mail', 'odoo.models.unlink', 'odoo.tests')
    @users('employee')
    @warmup
    def test_article_invite_members(self):
        with self.assertQueryCount(employee=79):
            shared_article = self.shared_children[0].with_env(self.env)
            partners = (self.customer + self.partner_employee_manager + self.partner_employee2).with_env(self.env)
            shared_article.invite_members(partners, 'write')

    @users('employee')
    @warmup
    def test_article_move_to(self):
        before_id = self.workspace_children[0].id
        with self.assertQueryCount(employee=24):  # knowledge: 23
            writable_article = self.workspace_children[1].with_env(self.env)
            writable_article.move_to(parent_id=writable_article.parent_id.id, before_article_id=before_id)

    @users('employee')
    @warmup
    def test_get_user_sorted_articles(self):
        with self.assertQueryCount(employee=5):
            self.env['knowledge.article'].get_user_sorted_articles('')

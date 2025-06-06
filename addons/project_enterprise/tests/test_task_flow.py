# Part of Odoo. See LICENSE file for full copyright and licensing details

from datetime import datetime
from dateutil.relativedelta import relativedelta

from odoo.tests import common
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT

from odoo.addons.mail.tests.common import mail_new_test_user


class TestTaskFlow(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.resource_calendar_id.tz = "Europe/Brussels"
        cls.project_user = mail_new_test_user(
            cls.env, login='Armande',
            name='Armande Project_user', email='armande.project_user@example.com',
            notification_type='inbox',
            groups='project.group_project_user',
            resource_calendar_id=cls.env.company.resource_calendar_id.id,
        )

        cls.project_test_user = mail_new_test_user(
            cls.env, login='Armando',
            name='Armando Project_user', email='armando.project_user@example.com',
            notification_type='inbox',
            groups='project.group_project_user',
        )

        cls.project_test = cls.env['project.project'].create({
            'name': 'Project Test',
        })

        cls.portal_user = mail_new_test_user(
            cls.env, login='portal_project',
            name='Portal_user', email='portal_project_user@example.com',
            notification_type='email',
            groups='base.group_portal',
        )

    def create_tasks(self, nb=40):
        now = datetime.combine(datetime.now(), datetime.min.time())
        hour_start = [6, 11]
        hour_end = [10, 15]
        users = [self.project_test_user, self.project_user, self.project_user | self.project_test_user]

        self.env['project.task'].with_context(tracking_disable=True).create([{
            'name': 'Fsm task ' + str(i),
            'user_ids': users[i % 3],
            'project_id': self.project_test.id,
            'planned_date_begin': now + relativedelta(days=i / 2, hour=hour_start[i % 2]),
            'planned_date_end': now + relativedelta(days=i / 2, hour=hour_end[i % 2])
        } for i in range(0, nb)])

    def test_planning_overlap(self):
        task_A = self.env['project.task'].create({
            'name': 'Fsm task 1',
            'user_ids': self.project_user,
            'project_id': self.project_test.id,
            'planned_date_begin': datetime.now(),
            'planned_date_end': datetime.now() + relativedelta(hours=4)
        })
        task_B = self.env['project.task'].create({
            'name': 'Fsm task 2',
            'user_ids': self.project_user,
            'project_id': self.project_test.id,
            'planned_date_begin': datetime.now() + relativedelta(hours=2),
            'planned_date_end': datetime.now() + relativedelta(hours=6)
        })
        task_C = self.env['project.task'].create({
            'name': 'Fsm task 2',
            'user_ids': self.project_user,
            'project_id': self.project_test.id,
            'planned_date_begin': datetime.now() + relativedelta(hours=5),
            'planned_date_end': datetime.now() + relativedelta(hours=7)
        })
        task_D = self.env['project.task'].create({
            'name': 'Fsm task 2',
            'user_ids': self.project_user,
            'project_id': self.project_test.id,
            'planned_date_begin': datetime.now() + relativedelta(hours=8),
            'planned_date_end': datetime.now() + relativedelta(hours=9)
        })
        self.assertEqual(task_A.planning_overlap, 1, "One task should be overlapping with task_A")
        self.assertEqual(task_B.planning_overlap, 2, "Two tasks should be overlapping with task_B")
        self.assertFalse(task_D.planning_overlap, "No task should be overlapping with task_D")

    def test_gantt_progress_bar(self):
        self.env['project.task'].create([{
            'name': 'Task 1',
            'user_ids': self.project_user,
            'project_id': self.project_test.id,
            'planned_date_begin': '2021-09-24 06:00:00',
            'planned_date_end': '2021-09-24 15:00:00',
        }, {
            'name': 'Task 2',
            'user_ids': self.project_user,
            'project_id': self.project_test.id,
            'planned_date_begin': '2021-09-27 06:00:00',
            'planned_date_end': '2021-09-28 15:00:00',
        }, {
            'name': 'Task 3',
            'user_ids': self.project_user,
            'project_id': self.project_test.id,
            'planned_date_begin': '2021-09-29 05:00:00',
            'planned_date_end': '2021-09-29 08:00:00',
        }, {
            'name': 'Task 4',
            'user_ids': self.project_user,
            'project_id': self.project_test.id,
            'planned_date_begin': '2021-09-30 12:00:00',
            'planned_date_end': '2021-09-30 15:00:00',
        }])

        progress_bar = self.env['project.task'].gantt_progress_bar(
            ['user_ids'], {'user_ids': self.project_user.ids}, '2021-09-26 00:00:00', '2021-10-02 23:59:59'
        )['user_ids']
        self.assertEqual(22, progress_bar[self.project_user.id]['value'], "User should have 22 hours planned on this period")
        self.assertEqual(40, progress_bar[self.project_user.id]['max_value'], "User is expected to work 40 hours on this period")

        self.env['project.task'].create([{
            'name': 'Task 1',
            'user_ids': self.project_user,
            'project_id': self.project_test.id,
            'planned_date_begin': '2021-10-02 08:00:00',
            'planned_date_end': '2021-10-02 17:00:00',
        }])

        progress_bar = self.env['project.task'].gantt_progress_bar(
            ['user_ids'], {'user_ids': self.project_user.ids}, '2021-09-26 00:00:00', '2021-10-02 23:59:59'
        )['user_ids']
        self.assertEqual(31, progress_bar[self.project_user.id]['value'], "User should have 31 hours planned on this period")
        self.assertEqual(40, progress_bar[self.project_user.id]['max_value'], "User is expected to work 40 hours on this period")

        self.env['project.task'].create([{
            'name': 'Task 2',
            'user_ids': self.project_user,
            'project_id': self.project_test.id,
            'planned_date_begin': '2021-09-24 08:00:00',
            'planned_date_end': '2021-09-27 17:00:00',
        }])

        progress_bar = self.env['project.task'].gantt_progress_bar(
            ['user_ids'], {'user_ids': self.project_user.ids}, '2021-09-26 00:00:00', '2021-10-02 23:59:59'
        )['user_ids']
        self.assertEqual(39, progress_bar[self.project_user.id]['value'], "User should have 39 hours planned on this period")
        self.assertEqual(40, progress_bar[self.project_user.id]['max_value'], "User is expected to work 40 hours on this period")

    def test_project_user_can_see_progress_bar(self):
        self.env['project.task'].create([{
            'name': 'Task 1',
            'user_ids': self.project_user,
            'project_id': self.project_test.id,
            'planned_date_begin': '2021-09-27 06:00:00',
            'planned_date_end': '2021-09-28 15:00:00',
        }])

        progress_bar = self.env['project.task'].with_user(self.project_test_user).gantt_progress_bar(
            ['user_ids'], {'user_ids': self.project_user.ids}, '2021-09-26 00:00:00', '2021-10-02 23:59:59'
        )['user_ids']
        self.assertEqual(16, progress_bar[self.project_user.id]['value'], "User should have 22 hours planned on this period")
        self.assertEqual(40, progress_bar[self.project_user.id]['max_value'], "User is expected to work 40 hours on this period")

    def test_portal_user_cannot_see_progress_bar(self):
        self.env['project.task'].create([{
            'name': 'Task 1',
            'user_ids': self.project_user,
            'project_id': self.project_test.id,
            'planned_date_begin': '2021-09-27 06:00:00',
            'planned_date_end': '2021-09-28 15:00:00',
        }])

        progress_bar = self.env['project.task'].with_user(self.portal_user).gantt_progress_bar(
            ['user_ids'], {'user_ids': self.project_user.ids}, '2021-09-26 00:00:00', '2021-10-02 23:59:59'
        )['user_ids']
        self.assertFalse(progress_bar, "Progress bar should be empty for non-project users")

    def test_planned_date_consistency_for_tasks(self):
        """ This test ensures that a task can not have date start set, if its date end is False and that it can not have a date end set if its date start is False """
        task_1 = self.env['project.task'].create([{
            'name': 'Task 1',
            'user_ids': self.project_user,
            'project_id': self.project_test.id,
            'planned_date_begin': '2021-09-27 06:00:00',
            'planned_date_end': '2021-09-28 15:00:00',
        }])

        task_1.planned_date_begin = False
        self.assertFalse(task_1.planned_date_begin, 'the planned date begin should be set to False')
        self.assertFalse(task_1.planned_date_end, 'the planned date end should be set to False')

        task_1.write({'planned_date_begin': '2021-09-27 06:00:00', 'planned_date_end': '2021-09-28 15:00:00'})
        self.assertEqual('2021-09-27', task_1.planned_date_begin.strftime('%Y-%m-%d'), 'the planned date begin should be set to the new date')
        self.assertEqual('2021-09-28', task_1.planned_date_end.strftime('%Y-%m-%d'), 'the planned date end should be set to the new date')

        task_1.planned_date_end = False
        self.assertFalse(task_1.planned_date_begin, 'the planned date begin should be set to False')
        self.assertFalse(task_1.planned_date_end, 'the planned date end should be set to False')

        task_1.write({'planned_date_begin': '2021-09-27 06:00:00'})
        self.assertFalse(task_1.planned_date_begin, 'the planned date begin should not be updated')
        self.assertFalse(task_1.planned_date_end, 'the planned date end should not be updated')

        task_1.write({'planned_date_end': '2021-09-27 06:00:00'})
        self.assertFalse(task_1.planned_date_begin, 'the planned date begin should not be updated')
        self.assertFalse(task_1.planned_date_end, 'the planned date end should not be updated')

        # Test write on a recordset.
        task_2 = self.env['project.task'].create([{
            'name': 'Task 2',
            'user_ids': self.project_user,
            'project_id': self.project_test.id,
            'planned_date_begin': '2021-09-23 06:00:00',
            'planned_date_end': '2021-09-24 15:00:00',
        }])
        tasks = task_1 | task_2

        # Case 1 : one record has a daterange set, one record has no daterange.
        # Set a new daterange. both should be updated.
        tasks.write({'planned_date_begin': '2021-09-27 06:00:00', 'planned_date_end': '2021-09-28 15:00:00'})
        for task in tasks:
            self.assertEqual('2021-09-27', task.planned_date_begin.strftime('%Y-%m-%d'), 'the planned date begin should be set to the new date')
            self.assertEqual('2021-09-28', task.planned_date_end.strftime('%Y-%m-%d'), 'the planned date end should be set to the new date')
        task_1.write({'planned_date_begin': False})
        # Set the date start to a new date. None of the tasks should be updated.
        tasks.write({'planned_date_begin': '2021-09-23 06:00:00'})
        self.assertFalse(task_1.planned_date_begin, 'the planned date begin should not be updated')
        self.assertFalse(task_1.planned_date_end, 'the planned date end should not be updated')
        self.assertEqual('2021-09-27', task_2.planned_date_begin.strftime('%Y-%m-%d'), 'the planned date begin should not be updated')
        self.assertEqual('2021-09-28', task_2.planned_date_end.strftime('%Y-%m-%d'), 'the planned date end should not be updated')
        # Set the date end to a new date. None of the tasks should be updated.
        tasks.write({'planned_date_end': '2021-09-30 06:00:00'})
        self.assertFalse(task_1.planned_date_begin, 'the planned date begin should not be updated')
        self.assertFalse(task_1.planned_date_end, 'the planned date end should not be updated')
        self.assertEqual('2021-09-27', task_2.planned_date_begin.strftime('%Y-%m-%d'), 'the planned date begin should not be updated')
        self.assertEqual('2021-09-28', task_2.planned_date_end.strftime('%Y-%m-%d'), 'the planned date end should not be updated')
        # Set the date start to False. Both tasks should have a daterange set to False.
        tasks.write({'planned_date_begin': False})
        for task in tasks:
            self.assertFalse(task.planned_date_begin, 'the planned date begin should be set to False')
            self.assertFalse(task.planned_date_end, 'the planned date end should be set to False')
        task_2.write({'planned_date_begin': '2021-09-27 06:00:00', 'planned_date_end': '2021-09-28 15:00:00'})
        # Set the date end to False. Both tasks should have a daterange set to False.
        tasks.write({'planned_date_end': False})
        for task in tasks:
            self.assertFalse(task.planned_date_begin, 'the planned date begin should be set to False')
            self.assertFalse(task.planned_date_end, 'the planned date end should be set to False')

        # Case 2 both record have no daterange
        # Set the date start to a new date. None of the tasks should be updated.
        tasks.write({'planned_date_begin': '2021-09-23 06:00:00'})
        for task in tasks:
            self.assertFalse(task.planned_date_begin, 'the planned date begin should not be updated')
            self.assertFalse(task.planned_date_end, 'the planned date end should not be updated')
        # Set the date end to a new date. None of the tasks should be updated.
        tasks.write({'planned_date_end': '2021-09-30 06:00:00'})
        for task in tasks:
            self.assertFalse(task.planned_date_begin, 'the planned date begin should not be updated')
            self.assertFalse(task.planned_date_end, 'the planned date end should not be updated')
        # Set the date start to False. None of the tasks should be updated.
        tasks.write({'planned_date_begin': False})
        for task in tasks:
            self.assertFalse(task.planned_date_begin, 'the planned date begin should not be updated')
            self.assertFalse(task.planned_date_end, 'the planned date end should not be updated')
        # Set the date end to False. None of the tasks should be updated.
        tasks.write({'planned_date_end': False})
        for task in tasks:
            self.assertFalse(task.planned_date_begin, 'the planned date begin should not be updated')
            self.assertFalse(task.planned_date_end, 'the planned date end should not be updated')
        # Set a new daterange. Both tasks should be updated.
        tasks.write({'planned_date_begin': '2021-09-27 06:00:00', 'planned_date_end': '2021-09-28 15:00:00'})
        for task in tasks:
            self.assertEqual('2021-09-27', task.planned_date_begin.strftime('%Y-%m-%d'), 'the planned date begin should be set to the new date')
            self.assertEqual('2021-09-28', task.planned_date_end.strftime('%Y-%m-%d'), 'the planned date end should be set to the new date')

        # Case 3 both record have a different daterange set
        task_2.write({'planned_date_begin': '2021-09-23 06:00:00', 'planned_date_end': '2021-09-24 15:00:00'})
        # Set the date start to a new date. Both tasks should be updated.
        tasks.write({'planned_date_begin': '2021-09-22 06:00:00'})
        self.assertEqual('2021-09-22', task_1.planned_date_begin.strftime('%Y-%m-%d'), 'the planned date begin should be set to the new date')
        self.assertEqual('2021-09-28', task_1.planned_date_end.strftime('%Y-%m-%d'), 'the planned date end should not be updated')
        self.assertEqual('2021-09-22', task_2.planned_date_begin.strftime('%Y-%m-%d'), 'the planned date begin should be set to the new date')
        self.assertEqual('2021-09-24', task_2.planned_date_end.strftime('%Y-%m-%d'), 'the planned date end should not be updated')
        task_2.write({'planned_date_begin': '2021-09-23 06:00:00'})
        # Set the date end to a new date. None of the tasks should be updated.
        tasks.write({'planned_date_end': '2021-09-30 06:00:00'})
        self.assertEqual('2021-09-22', task_1.planned_date_begin.strftime('%Y-%m-%d'), 'the planned date begin should not be updated')
        self.assertEqual('2021-09-30', task_1.planned_date_end.strftime('%Y-%m-%d'), 'the planned date end should be set to the new date')
        self.assertEqual('2021-09-23', task_2.planned_date_begin.strftime('%Y-%m-%d'), 'the planned date begin should not be updated')
        self.assertEqual('2021-09-30', task_2.planned_date_end.strftime('%Y-%m-%d'), 'the planned date end should be set to the new date')
        task_1.write({'planned_date_begin': '2021-09-27 06:00:00', 'planned_date_end': '2021-09-28 15:00:00'})
        task_2.write({'planned_date_begin': '2021-09-23 06:00:00', 'planned_date_end': '2021-09-24 15:00:00'})
        # Set the date start to False. Both tasks should be set to False.
        tasks.write({'planned_date_begin': False})
        for task in tasks:
            self.assertFalse(task.planned_date_begin, 'the planned date begin should be set to False')
            self.assertFalse(task.planned_date_end, 'the planned date end should be set to False')
        task_1.write({'planned_date_begin': '2021-09-27 06:00:00', 'planned_date_end': '2021-09-28 15:00:00'})
        task_2.write({'planned_date_begin': '2021-09-23 06:00:00', 'planned_date_end': '2021-09-24 15:00:00'})
        # Set the date end to False. Both tasks should be updated.
        tasks.write({'planned_date_end': False})
        for task in tasks:
            self.assertFalse(task.planned_date_begin, 'the planned date begin should be set to False')
            self.assertFalse(task.planned_date_end, 'the planned date end should be set to False')
        task_1.write({'planned_date_begin': '2021-09-27 06:00:00', 'planned_date_end': '2021-09-28 15:00:00'})
        task_2.write({'planned_date_begin': '2021-09-23 06:00:00', 'planned_date_end': '2021-09-24 15:00:00'})
        # Set a new daterange. Both should be updated.
        tasks.write({'planned_date_begin': '2021-09-25 06:00:00', 'planned_date_end': '2021-09-26 15:00:00'})
        for task in tasks:
            self.assertEqual('2021-09-25', task.planned_date_begin.strftime('%Y-%m-%d'), 'the planned date begin should be set to the new date')
            self.assertEqual('2021-09-26', task.planned_date_end.strftime('%Y-%m-%d'), 'the planned date end should be set to the new date')

    def test_performance(self):
        nb = 40
        self.create_tasks(nb=nb)
        start = datetime.combine(datetime.now(), datetime.min.time()) + relativedelta(days=-1)
        end = start + relativedelta(days=nb / 2 + 1)
        users = self.project_user | self.project_test_user

        with self.assertQueryCount(__system__=10):
            # Query count should be stable even if the number of tasks or users increase (progress bar query count is O(1))
            progress_bar = self.env['project.task'].gantt_progress_bar(
                ['user_ids'], {'user_ids': users.ids}, start.strftime(DEFAULT_SERVER_DATETIME_FORMAT), end.strftime(DEFAULT_SERVER_DATETIME_FORMAT)
            )['user_ids']

        self.assertEqual(len(progress_bar), 3)  # 2 users + 1 warning

    def test_default_planned_dates(self):
        def get_hours(task):
            return task.planned_date_begin.hour, task.planned_date_end.hour

        task_default, task_ot, task_plan = self.env['project.task'].create([{
            'name': "Regular task",
            'user_ids': self.project_user.ids,
            'project_id': self.project_test.id,
        }, {
            'name': "Overtime task",
            'user_ids': self.project_user.ids,
            'project_id': self.project_test.id,
        }, {
            'name': "Planned task",
            'user_ids': self.project_user.ids,
            'project_id': self.project_test.id,
            # Wednesday 05:00:00 -> 10:00:00 CET
            'planned_date_begin': '2024-08-27 03:00:00',
            'planned_date_end': '2024-08-27 08:00:00',
        }])

        self.assertEqual(get_hours(task_plan), (3, 8), "No overwrite on create")

        (task_default + task_plan).write({
            # Monday 00:00:00 -> 23:59:59 CET
            'planned_date_begin': '2024-08-25 22:00:00',
            'planned_date_end': '2024-08-26 21:59:59',
        })
        self.assertEqual(get_hours(task_default), (6, 15), "Default hours should change")
        self.assertEqual(get_hours(task_plan), (22, 21), "Write as is on pre-existing values")

        task_ot.write({
            # Tuesday 16:00:00 -> 21:00:00 CET
            'planned_date_begin': '2024-08-27 14:00:00',
            'planned_date_end': '2024-08-27 19:00:00',
        })
        self.assertEqual(get_hours(task_ot), (14, 19), "Hand-picked hours shouldn't change")

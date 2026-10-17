from django.test import Client, TestCase
from django.urls import reverse

from .models import ParentChild, Subject, User
from .services import create_task, save_draft, submit_task


class ParentTaskActionsTests(TestCase):
    def setUp(self):
        self.parent = User.objects.create_user('actions-parent', is_parent=True)
        self.child = User.objects.create_user('actions-child', is_student=True)
        ParentChild.objects.create(parent=self.parent, child=self.child)
        self.parent.refresh_from_db()
        self.subject = Subject.objects.create(name='数学')
        self.task = create_task(self.parent, self.child, '列表操作任务', self.subject)
        self.url = reverse('task_delete', args=[self.task.pk])
        self.list_url = reverse('tasks') + f'?child={self.child.pk}'
        self.client.force_login(self.parent)

    def test_parent_focus_has_working_view_edit_and_delete_entries(self):
        response = self.client.get(self.list_url)
        self.assertContains(response, reverse('task_detail', args=[self.task.pk]))
        self.assertContains(response, reverse('task_edit', args=[self.task.pk]))
        self.assertContains(response, self.url)
        self.assertContains(response, 'aria-label="查看任务"')
        self.assertContains(response, 'aria-label="编辑任务"')
        self.assertContains(response, 'aria-label="删除任务"')

    def test_delete_hides_task_and_keeps_selected_child_and_filter(self):
        response = self.client.post(self.url + '?phase=draft&page=1')
        self.assertRedirects(response, self.list_url + '&phase=draft&page=1')
        self.task.refresh_from_db()
        self.assertTrue(self.task.deleted)
        self.assertNotContains(self.client.get(self.list_url), self.task.title)
        deleted_response = self.client.get(reverse('task_detail', args=[self.task.pk]))
        self.assertEqual(deleted_response.status_code, 410)
        self.assertContains(deleted_response, '任务已被删除', status_code=410)

    def test_delete_requires_post_and_csrf(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.parent)
        self.assertEqual(client.post(self.url).status_code, 403)
        self.task.refresh_from_db()
        self.assertFalse(self.task.deleted)

    def test_student_and_unrelated_parent_cannot_delete(self):
        other = User.objects.create_user('actions-other', is_parent=True)
        for actor in (self.child, other):
            self.client.force_login(actor)
            self.assertEqual(self.client.post(self.url).status_code, 403)
        self.client.force_login(self.child)
        response = self.client.get(self.list_url)
        self.assertNotContains(response, self.url)
        self.assertNotContains(response, reverse('task_edit', args=[self.task.pk]))
        self.task.refresh_from_db()
        self.assertFalse(self.task.deleted)

    def test_formally_submitted_task_cannot_be_deleted(self):
        save_draft(self.child, self.task.pk, result='完成')
        submit_task(self.child, self.task.pk)
        response = self.client.get(self.list_url)
        self.assertNotContains(response, self.url)
        self.assertContains(response, '正式提交或已完结的任务不能删除')
        response = self.client.post(self.url, follow=True)
        self.assertContains(response, '正式提交后的任务不能删除')
        self.task.refresh_from_db()
        self.assertFalse(self.task.deleted)

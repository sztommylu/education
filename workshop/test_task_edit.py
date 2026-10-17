import json
from copy import deepcopy
from django.test import TestCase
from django.urls import reverse
from .models import User, ParentChild, Subject, Task, ReviewJob
from .services import create_task, task_edit_version, save_draft, submit_task, save_handwriting_answer


class ParentTaskEditTests(TestCase):
    def setUp(self):
        self.parent = User.objects.create_user('edit-parent', is_parent=True)
        self.child = User.objects.create_user('edit-child', is_student=True)
        ParentChild.objects.create(parent=self.parent, child=self.child)
        self.parent.refresh_from_db()
        self.subject = Subject.objects.create(name='英语')
        self.assignment = {'version':1,'course':'英语','category':'vocabulary','category_label':'字词',
                           'content':{'words':'apple:苹果','phrases':'','sentences':''}}
        self.task = create_task(self.parent, self.child, 'Unit1', self.subject, assignment=self.assignment)
        self.url = reverse('task_edit', args=[self.task.pk])
        self.detail = reverse('task_detail', args=[self.task.pk])
        self.client.force_login(self.parent)

    def payload(self, **changes):
        self.task.refresh_from_db()
        data = {'version':task_edit_version(self.task), 'title':'Unit1 修改', 'kind':'daily',
                'requirements':'认真默写', 'english_category':'vocabulary','words':'1. apple##苹果##\n2. book##书##'}
        data.update(changes)
        return data

    def test_edit_entry_and_prefilled_form(self):
        self.assertNotContains(self.client.get(self.detail), self.url)
        self.assertContains(self.client.get(reverse('tasks')), self.url)
        response = self.client.get(self.url)
        self.assertContains(response, '编辑任务 · Unit1')
        self.assertContains(response, '保存修改')
        self.assertEqual(response.context['form']['words'].value(), 'apple:苹果')
        self.assertEqual(response.context['form']['title'].value(), 'Unit1')
        self.assertNotContains(response, 'name="attachment"')

    def test_edit_changes_same_task_and_student_sees_new_chinese_questions(self):
        self.assertRedirects(self.client.post(self.url, self.payload()), self.detail)
        self.task.refresh_from_db()
        self.assertEqual(Task.objects.count(), 1)
        self.assertEqual(self.task.title, 'Unit1 修改')
        self.assertIn('book##书##', self.task.template_snapshot['requirements'])
        self.assertEqual(self.task.template_snapshot['requirements'].count('家长布置内容'), 1)
        self.assertEqual(self.task.handwriting_draft['revision'], 1)
        self.client.force_login(self.child)
        response = self.client.get(self.detail)
        self.assertContains(response, '书')
        self.assertNotContains(response, 'book')
        self.assertNotContains(response, self.url)

    def test_edit_rejects_missing_order_without_changing_task(self):
        response = self.client.post(self.url, self.payload(words='book##书##'))
        self.assertContains(response, 'book前面没有带上有序符号')
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, 'Unit1')
        self.assertEqual(self.task.template_snapshot['assignment'], self.assignment)

    def test_started_tasks_keep_question_mapping_but_title_and_requirements_are_editable(self):
        save_handwriting_answer(self.child, self.task.pk, 0,
            json.dumps([{'strokes':[{'width':5,'points':[[50,50],[100,100]]}]}]), 0)
        response = self.client.post(self.url, self.payload())
        self.assertContains(response, '学生已开始作答，不能修改题目内容')
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, 'Unit1')
        response = self.client.post(self.url, self.payload(words='1. apple##苹果##'))
        self.assertRedirects(response, self.detail)
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, 'Unit1 修改')
        self.assertEqual(self.task.handwriting_draft['saved_indices'], [0])

    def test_editing_title_and_requirements_never_mutates_formal_snapshot(self):
        save_draft(self.child, self.task.pk, result='已完成')
        sub = submit_task(self.child, self.task.pk)
        snapshot = deepcopy(sub.snapshot)
        self.assertRedirects(self.client.post(self.url, self.payload(words='1. apple##苹果##')), self.detail)
        sub.refresh_from_db()
        self.assertEqual(sub.snapshot, snapshot)
        self.assertEqual(sub.requirements, '')
        self.assertRedirects(self.client.get(self.detail + '?review=1'), self.detail)

    def test_stale_editor_is_rejected_without_overwriting_new_changes(self):
        payload = self.payload(words='1. apple##苹果##')
        self.task.title = '另一个页面更新'
        self.task.save(update_fields=['title'])
        self.assertContains(self.client.post(self.url, payload), '任务或学生进度已更新')
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, '另一个页面更新')

    def test_new_questions_invalidate_open_student_answer_page(self):
        self.client.post(self.url, self.payload())
        self.client.force_login(self.child)
        response = self.client.post(self.detail, {'operation':'save_handwriting_answer','index':0,'revision':0,
            'handwriting':json.dumps([{'strokes':[{'width':5,'points':[[50,50]]}]}])})
        self.assertEqual(response.status_code, 400)
        self.task.refresh_from_db()
        self.assertEqual(self.task.handwriting_draft['saved_indices'], [])

    def test_student_unrelated_parent_and_closed_tasks_cannot_edit(self):
        other = User.objects.create_user('edit-other-parent', is_parent=True)
        for actor in [self.child, other]:
            self.client.force_login(actor)
            self.assertEqual(self.client.get(self.url).status_code, 403)
            self.assertEqual(self.client.post(self.url, self.payload()).status_code, 403)
        self.client.force_login(self.parent)
        self.task.phase = 'awarded'
        self.task.save(update_fields=['phase'])
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 403)

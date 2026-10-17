from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from .models import ParentChild, Subject, TaskTemplate, User, Wallet, Settlement
from .services import create_task, save_draft, submit_task, record_review, decide_task


class ParentTaskDecisionTests(TestCase):
    def setUp(self):
        self.parent = User.objects.create_user('decision-parent', is_parent=True)
        self.child = User.objects.create_user('decision-child', is_student=True)
        ParentChild.objects.create(parent=self.parent, child=self.child)
        self.parent.refresh_from_db()
        self.subject = Subject.objects.create(name='数学')
        self.template = TaskTemplate.objects.create(child=self.child, subject=self.subject, title='任务模板')
        self.task = create_task(self.parent, self.child, '状态流程', self.subject, template=self.template)
        self.url = reverse('task_decision', args=[self.task.pk])
        self.list_url = reverse('tasks') + f'?child={self.child.pk}'
        self.client.force_login(self.parent)

    def submit(self):
        save_draft(self.child, self.task.pk, result='学生答案')
        sub = submit_task(self.child, self.task.pk)
        self.task.refresh_from_db()
        return sub

    def review(self, sub, grade='B'):
        return record_review(sub.pk, 'ai', {'grade':grade, 'reason':'批改建议', 'suggestions':'', 'knowledge':[], 'mistakes':[]})

    def test_ai_good_or_bad_score_stays_waiting_without_reward(self):
        for grade in ('A', 'B', 'C', 'D'):
            self.review(self.submit(), grade)
            self.task.refresh_from_db()
            self.assertEqual(self.task.phase, 'waiting')
            self.assertFalse(Settlement.objects.exists())

    def test_return_labels_and_resubmission_flow(self):
        self.assertEqual(self.task.phase, 'draft')
        sub = self.submit()
        self.assertRedirects(self.client.post(self.url, {'version':sub.pk, 'decision':'revision'}), self.list_url)
        self.task.refresh_from_db()
        self.assertEqual(self.task.phase, 'revision')
        self.assertContains(self.client.get(self.list_url), '>待修改</span>')
        self.client.force_login(self.child)
        self.assertContains(self.client.get(self.list_url), '>被驳回</span>')
        detail = reverse('task_detail', args=[self.task.pk])
        self.assertContains(self.client.get(detail), '家长退回修改 · 第 1 版')
        self.review(sub)
        self.task.refresh_from_db()
        self.assertEqual(self.task.phase, 'revision')
        newer = self.submit()
        self.assertEqual(newer.number, 2)
        self.assertNotContains(self.client.get(detail), '家长退回修改')
        self.assertEqual(self.task.final_feedback.count(), 1)
        self.assertEqual(self.task.phase, 'waiting')
        self.client.force_login(self.parent)
        self.assertContains(self.client.get(self.list_url), '>等待审核</span>')
        self.assertNotContains(self.client.get(detail), '家长退回修改')

    def test_other_subject_completion_does_not_add_rewards_and_filters_completed(self):
        sub = self.submit()
        self.review(sub)
        response = self.client.post(self.url, {'version':sub.pk,'decision':'complete'})
        self.assertRedirects(response, self.list_url)
        self.task.refresh_from_db()
        self.assertTrue(self.task.closed)
        self.assertEqual(self.task.phase, 'awarded')
        self.assertEqual(Wallet.objects.get(child=self.child).points,0)
        self.client.post(self.url, {'version':sub.pk,'decision':'complete'})
        self.assertEqual(Settlement.objects.count(), 1)
        self.assertContains(self.client.get(self.list_url + '&phase=awarded'), self.task.title)
        for actor in (self.parent,self.child):
            self.client.force_login(actor)
            self.assertContains(self.client.get(self.list_url), '>完成</span>')

    def test_parent_can_finish_without_ai_and_late_ai_does_not_change_decision(self):
        sub = self.submit()
        decide_task(self.parent,self.task.pk,sub.pk,'complete')
        self.review(sub,'D')
        self.task.refresh_from_db()
        self.assertEqual(self.task.phase,'awarded')
        self.assertEqual(Settlement.objects.get().grade,'C')

    def test_parent_cannot_decide_unsubmitted_task_or_stale_submission(self):
        with self.assertRaises(ValidationError):
            decide_task(self.parent,self.task.pk,0,'complete')
        first = self.submit()
        self.submit()
        with self.assertRaises(ValidationError):
            decide_task(self.parent,self.task.pk,first.pk,'revision')
        self.task.refresh_from_db()
        self.assertEqual(self.task.phase,'waiting')

    def test_access_and_csrf_and_post_required(self):
        sub = self.submit()
        self.assertEqual(self.client.get(self.url).status_code,405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.parent)
        self.assertEqual(csrf_client.post(self.url,{'version':sub.pk,'decision':'complete'}).status_code,403)
        other = User.objects.create_user('decision-other',is_parent=True)
        for actor in (self.child,other):
            self.client.force_login(actor)
            self.assertEqual(self.client.post(self.url,{'version':sub.pk,'decision':'complete'}).status_code,403)
        self.assertFalse(Settlement.objects.exists())

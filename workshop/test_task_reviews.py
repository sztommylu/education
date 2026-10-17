from django.test import TestCase
from django.urls import reverse
from .models import ParentChild, Subject, User
from .services import create_task, save_draft, submit_task


class RemovedParentReviewTests(TestCase):
    def setUp(self):
        self.parent = User.objects.create_user('review-parent', is_parent=True)
        self.child = User.objects.create_user('review-child', is_student=True)
        ParentChild.objects.create(parent=self.parent, child=self.child)
        self.parent.refresh_from_db()
        self.task = create_task(self.parent, self.child, '查看任务', Subject.objects.create(name='数学'))
        save_draft(self.child, self.task.pk, result='完成')
        self.submission = submit_task(self.child, self.task.pk)
        self.detail = reverse('task_detail', args=[self.task.pk])
        self.client.force_login(self.parent)

    def test_parent_page_has_no_review_entries_or_controls(self):
        response = self.client.get(self.detail)
        for text in ('任务审核', '家长初审', '家长最终复核', '保存初审', '确认复核'):
            self.assertNotContains(response, text)
        self.assertEqual(self.client.get(reverse('task_reviews')).status_code, 410)
        self.assertRedirects(self.client.get(self.detail + '?review=1'), self.detail)

    def test_old_review_posts_cannot_change_submission_or_rewards(self):
        for operation in ('review', 'final'):
            response = self.client.post(self.detail, {
                'operation': operation, 'version': self.submission.pk,
                'grade': 'B', 'reason': '通过', 'action': 'settle', 'honor': 10, 'cents': 100,
            })
            self.assertEqual(response.status_code, 403)
        self.task.refresh_from_db()
        self.assertEqual(self.task.phase, 'waiting')
        self.assertTrue(self.task.versions.filter(pk=self.submission.pk).exists())
        self.assertFalse(hasattr(self.task, 'settlement'))

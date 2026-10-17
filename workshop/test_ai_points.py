from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from .models import User, ParentChild, Subject, Wallet, Settlement, PointEntry, Notification
from .services import create_task, save_draft, submit_task, record_review, decide_task, settle_ai_passed_review


class AIPassPointsTests(TestCase):
    def setUp(self):
        self.parent = User.objects.create_user('auto-parent',is_parent=True)
        self.child = User.objects.create_user('auto-child',is_student=True)
        ParentChild.objects.create(parent=self.parent,child=self.child)
        self.parent.refresh_from_db()
        self.subject = Subject.objects.create(name='英语')

    def submitted(self):
        task = create_task(self.parent,self.child,'英语自动积分',self.subject)
        save_draft(self.child,task.pk,result='学生答案')
        return task, submit_task(self.child,task.pk)

    def reviewed(self, sub, grade='C', model='real-model'):
        return record_review(sub.pk,'ai',{'grade':grade,'reason':'已完成要求','suggestions':'继续努力','knowledge':[],'mistakes':[]},model)

    def test_ai_pass_completes_and_awards_ten_points_exactly_once(self):
        for index, grade in enumerate(('A','B','C'),1):
            task, sub = self.submitted()
            self.reviewed(sub,grade)
            self.reviewed(sub,grade)
            task.refresh_from_db()
            self.assertEqual(task.phase,'awarded')
            settlement = Settlement.objects.get(task=task)
            self.assertEqual((settlement.source,settlement.parent_id,settlement.points),('ai',None,10))
            self.assertEqual(PointEntry.objects.get(task=task).points,10)
            self.assertEqual(Wallet.objects.get(child=self.child).points,index*10)
        self.assertEqual(Notification.objects.filter(kind='points').count(),3)

    def test_existing_waiting_passed_review_is_settled_once(self):
        task, sub = self.submitted()
        from .models import Task
        Task.objects.filter(pk=task.pk).update(phase='revision')
        self.reviewed(sub)
        self.assertFalse(Settlement.objects.exists())
        Task.objects.filter(pk=task.pk).update(phase='waiting')
        self.assertEqual(settle_ai_passed_review(sub.pk).points,10)
        self.assertIsNone(settle_ai_passed_review(sub.pk))
        self.assertEqual(Wallet.objects.get(child=self.child).points,10)

    def test_fail_and_development_mock_do_not_auto_pay(self):
        for grade, model in [('D','real-model'),('B','mock-v1')]:
            task, sub = self.submitted()
            self.reviewed(sub,grade,model)
            task.refresh_from_db()
            self.assertEqual(task.phase,'waiting')
        self.assertFalse(Settlement.objects.exists())
        self.assertFalse(PointEntry.objects.exists())

    def test_late_ai_cannot_override_return_or_pay_for_old_version(self):
        task, old = self.submitted()
        decide_task(self.parent,task.pk,old.pk,'revision')
        self.reviewed(old)
        task.refresh_from_db()
        self.assertEqual(task.phase,'revision')
        save_draft(self.child,task.pk,result='修改后的答案')
        newer = submit_task(self.child,task.pk)
        self.reviewed(old)
        self.assertFalse(PointEntry.objects.exists())
        self.reviewed(newer)
        self.assertEqual(PointEntry.objects.get(task=task).points,10)

    def test_parent_completes_first_then_late_ai_cannot_pay_twice(self):
        task, sub = self.submitted()
        decide_task(self.parent,task.pk,sub.pk,'complete')
        self.reviewed(sub)
        self.assertEqual(Wallet.objects.get(child=self.child).points,10)
        self.assertEqual(Settlement.objects.count(),1)
        self.assertEqual(Settlement.objects.get().source,'parent')

    def test_failed_ledger_rolls_back_ai_review_and_points(self):
        task, sub = self.submitted()
        with patch('workshop.services.PointEntry.objects.create',side_effect=RuntimeError('write failed')):
            with self.assertRaises(RuntimeError):self.reviewed(sub)
        self.assertFalse(hasattr(sub,'review'))
        self.assertFalse(Settlement.objects.exists())
        self.assertEqual(Wallet.objects.get(child=self.child).points,0)
        self.reviewed(sub)
        self.assertEqual(Wallet.objects.get(child=self.child).points,10)

    def test_parent_page_hides_letter_grade_and_shows_passed_result(self):
        task, sub = self.submitted()
        self.reviewed(sub,'B')
        self.client.force_login(self.parent)
        response = self.client.get(reverse('task_detail',args=[task.pk]))
        self.assertContains(response,'积分 +10')
        self.assertNotContains(response,'class="grade"')
        self.assertNotContains(response,'最终等级与初审等级存在差异')

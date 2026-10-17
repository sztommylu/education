from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from .models import User, ParentChild, Subject, TaskTemplate, Wallet, PointEntry, Settlement, Notification
from .services import create_task, save_draft, submit_task, record_review, decide_task


class EnglishTaskPointsTests(TestCase):
    def setUp(self):
        self.parent = User.objects.create_user('points-parent',is_parent=True)
        self.child = User.objects.create_user('points-child',is_student=True)
        ParentChild.objects.create(parent=self.parent,child=self.child)
        self.parent.refresh_from_db()
        self.subject = Subject.objects.create(name='英语')
        self.template = TaskTemplate.objects.create(child=self.child,subject=self.subject,title='英语任务')
        Wallet.objects.get_or_create(child=self.child)
        self.client.force_login(self.parent)

    def submitted(self, grade=None, subject=None):
        task = create_task(self.parent,self.child,'英语积分测试',subject or self.subject,
                           template=self.template if subject is None else None)
        save_draft(self.child,task.pk,result='完成内容')
        sub = submit_task(self.child,task.pk)
        if grade:
            record_review(sub.pk,'parent',{'grade':grade,'reason':'评分建议','suggestions':'','knowledge':[],'mistakes':[]})
        task.refresh_from_db()
        return task, sub

    def test_english_completion_adds_ten_points_regardless_of_grade(self):
        for index, grade in enumerate(('A','B','C','D',None),1):
            task, sub = self.submitted(grade)
            self.assertEqual(Wallet.objects.get(child=self.child).points,(index-1)*10)
            result = decide_task(self.parent,task.pk,sub.pk,'complete')
            self.assertEqual(result.points,10)
            self.assertEqual(PointEntry.objects.get(task=task).points,10)
        wallet = Wallet.objects.get(child=self.child)
        self.assertEqual((wallet.points,wallet.points_value_cents),(50,5000))
        self.assertEqual(Notification.objects.filter(kind='points').count(),5)

    def test_ledger_failure_rolls_back_points_settlement_and_task(self):
        task, sub = self.submitted('C')
        with patch('workshop.services.PointEntry.objects.create', side_effect=RuntimeError('storage failure')):
            with self.assertRaises(RuntimeError):
                decide_task(self.parent,task.pk,sub.pk,'complete')
        task.refresh_from_db()
        self.assertEqual(task.phase,'waiting')
        self.assertEqual(Wallet.objects.get(child=self.child).points,0)
        self.assertFalse(Settlement.objects.exists())
        self.assertFalse(Notification.objects.filter(kind='points').exists())

    def test_return_and_repeated_completion_never_duplicate_points(self):
        task, sub = self.submitted('C')
        decide_task(self.parent,task.pk,sub.pk,'revision')
        self.assertFalse(PointEntry.objects.exists())
        save_draft(self.child,task.pk,result='修改后的内容')
        new = submit_task(self.child,task.pk)
        decide_task(self.parent,task.pk,new.pk,'complete')
        with self.assertRaises(ValidationError):
            decide_task(self.parent,task.pk,new.pk,'complete')
        self.assertEqual(Wallet.objects.get(child=self.child).points,10)
        self.assertEqual(PointEntry.objects.count(),1)
        self.assertEqual(Settlement.objects.count(),1)

    def test_other_subject_does_not_earn_points_or_grade_rewards(self):
        task,sub = self.submitted('A',Subject.objects.create(name='数学'))
        decide_task(self.parent,task.pk,sub.pk,'complete')
        self.assertEqual(Wallet.objects.get(child=self.child).points,0)
        self.assertFalse(PointEntry.objects.exists())

    def test_parent_and_student_can_view_points_and_other_families_cannot(self):
        task,sub = self.submitted('B')
        self.assertRedirects(self.client.post(reverse('task_decision',args=[task.pk]),
            {'version':sub.pk,'decision':'complete','return_to':'detail','points':9999,'honor':9999,'cents':9999}),reverse('task_detail',args=[task.pk]))
        url = reverse('points')+f'?child={self.child.pk}'
        for actor in (self.parent,self.child):
            self.client.force_login(actor)
            response = self.client.get(url)
            self.assertContains(response,'+10 积分')
            self.assertContains(response,'1 积分＝1 元')
            self.assertContains(response,'¥10.00')
            self.assertContains(self.client.get(reverse('task_detail',args=[task.pk])),'积分 +10')
        other = User.objects.create_user('points-other-parent',is_parent=True)
        self.client.force_login(other)
        self.assertEqual(self.client.get(url).status_code,302)
        other_child = User.objects.create_user('points-other-child',is_student=True)
        ParentChild.objects.create(parent=other,child=other_child)
        other.refresh_from_db()
        self.client.force_login(other)
        self.assertEqual(self.client.get(url).status_code,404)
        self.client.force_login(self.child)
        self.assertEqual(self.client.get(reverse('points')+f'?child={other_child.pk}').context['child'],self.child)

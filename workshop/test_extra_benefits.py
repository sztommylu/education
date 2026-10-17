import uuid
from unittest.mock import patch
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from .models import User, ParentChild, ExtraBenefitGrant, Wallet, PointEntry, Notification
from .services import grant_extra_benefit


class ExtraBenefitTests(TestCase):
    def setUp(self):
        self.parent = User.objects.create_user('bonus-parent',is_parent=True)
        self.child = User.objects.create_user('bonus-child',is_student=True)
        ParentChild.objects.create(parent=self.parent,child=self.child)
        self.parent.refresh_from_db()
        self.wallet = Wallet.objects.create(child=self.child,points=10,frozen_points=4)
        self.client.force_login(self.parent)
        self.url = reverse('benefits')+f'?child={self.child.pk}'

    def test_manual_points_credit_and_idempotency(self):
        key = uuid.uuid4()
        grant = grant_extra_benefit(self.parent,self.child,'主动阅读',5,key)
        duplicate = grant_extra_benefit(self.parent,self.child,'主动阅读',5,key)
        self.assertEqual(duplicate.pk,grant.pk)
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.points,self.wallet.available_points,self.wallet.frozen_points),(15,11,4))
        self.assertEqual(PointEntry.objects.get(extra_benefit=grant).points,5)
        self.assertEqual(Notification.objects.filter(kind='points').count(),1)
        for reason, points in [('主动阅读',6),('换个事由',5)]:
            with self.assertRaises(ValidationError):grant_extra_benefit(self.parent,self.child,reason,points,key)
        self.assertEqual(ExtraBenefitGrant.objects.count(),1)

    def test_extra_benefits_can_be_given_multiple_times_without_daily_tasks(self):
        grant_extra_benefit(self.parent,self.child,'整理书桌',2,uuid.uuid4())
        grant_extra_benefit(self.parent,self.child,'帮助做饭',3,uuid.uuid4())
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.points,15)
        self.assertEqual(ExtraBenefitGrant.objects.count(),2)

    def test_access_and_invalid_points(self):
        other = User.objects.create_user('bonus-other',is_parent=True)
        for actor in (self.child,other):
            with self.assertRaises(PermissionDenied):grant_extra_benefit(actor,self.child,'奖励',5,uuid.uuid4())
        for points in (0,-1,1.5,True,1000001):
            with self.assertRaises(ValidationError):grant_extra_benefit(self.parent,self.child,'奖励',points,uuid.uuid4())
        self.assertFalse(ExtraBenefitGrant.objects.exists())

    def test_ledger_failure_rolls_back_credit_and_grant(self):
        with patch('workshop.services.PointEntry.objects.create',side_effect=RuntimeError('write failed')):
            with self.assertRaises(RuntimeError):grant_extra_benefit(self.parent,self.child,'奖励',5,uuid.uuid4())
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.points,10)
        self.assertFalse(ExtraBenefitGrant.objects.exists())
        self.assertFalse(Notification.objects.exists())

    def test_parent_form_navigation_and_student_ledger(self):
        response = self.client.get(self.url)
        self.assertContains(response,'额外福利')
        self.assertContains(response,'奖励积分')
        self.assertNotContains(response,'保存福利选择')
        response = self.client.post(self.url,{'reason':'主动阅读','points':5,'request_key':uuid.uuid4(),'child':self.child.pk})
        self.assertRedirects(response,self.url)
        self.assertContains(self.client.get(self.url),'主动阅读')
        self.client.force_login(self.child)
        self.assertEqual(self.client.get(self.url).status_code,403)
        response = self.client.get(reverse('points'))
        self.assertContains(response,'额外福利')
        self.assertContains(response,'+5 积分')
        self.assertContains(self.client.get(reverse('records')),'主动阅读')

    def test_csrf_and_other_child_access(self):
        client = Client(enforce_csrf_checks=True);client.force_login(self.parent)
        self.assertEqual(client.post(self.url,{'reason':'奖励','points':5,'request_key':uuid.uuid4()}).status_code,403)
        other_child = User.objects.create_user('bonus-other-child',is_student=True)
        self.assertEqual(self.client.get(reverse('benefits')+f'?child={other_child.pk}').status_code,404)

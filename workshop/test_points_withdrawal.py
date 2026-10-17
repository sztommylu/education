import uuid
from unittest.mock import patch
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from .models import User, ParentChild, Wallet, Withdrawal, PointEntry
from .services import request_withdrawal, handle_withdrawal


class PointsWithdrawalTests(TestCase):
    def setUp(self):
        self.parent = User.objects.create_user('withdraw-parent',is_parent=True)
        self.child = User.objects.create_user('withdraw-child',is_student=True)
        ParentChild.objects.create(parent=self.parent,child=self.child)
        self.parent.refresh_from_db()
        self.wallet = Wallet.objects.create(child=self.child,points=10)
        self.url = reverse('wallet')+f'?child={self.child.pk}'
        self.client.force_login(self.child)

    def apply(self, points=6):
        return request_withdrawal(self.child,points*100,uuid.uuid4())

    def test_page_and_home_show_points_not_separate_cash_balance(self):
        response = self.client.get(self.url)
        self.assertContains(response,'¥ 10.00')
        self.assertContains(response,'可用积分 10')
        response = self.client.get(reverse('home'))
        self.assertContains(response,'10 <small>积分</small>')

    def test_application_freezes_points_and_repeats_are_idempotent(self):
        key = uuid.uuid4()
        obj = request_withdrawal(self.child,600,key)
        self.assertEqual(request_withdrawal(self.child,600,key).pk,obj.pk)
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.points,self.wallet.frozen_points,self.wallet.available_points),(10,6,4))
        with self.assertRaises(ValidationError):self.apply(5)
        with self.assertRaises(ValidationError):request_withdrawal(self.child,50,uuid.uuid4())
        self.assertContains(self.client.get(self.url),'¥ 4.00')
        self.assertContains(self.client.get(reverse('points')),'冻结 6 积分')

    def test_cancel_and_reject_release_points(self):
        for actor, action in [(self.child,'cancel'),(self.parent,'reject')]:
            obj = self.apply()
            handle_withdrawal(actor,obj.pk,action)
            self.wallet.refresh_from_db()
            self.assertEqual((self.wallet.points,self.wallet.frozen_points,self.wallet.available_points),(10,0,10))
        self.assertFalse(PointEntry.objects.exists())

    def test_confirm_payment_deducts_points_once_and_shows_debit_ledger(self):
        obj = self.apply()
        handle_withdrawal(self.parent,obj.pk,'pay')
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.points,self.wallet.frozen_points,self.wallet.available_points),(4,0,4))
        self.assertEqual(PointEntry.objects.get(withdrawal=obj).points,-6)
        with self.assertRaises(ValidationError):handle_withdrawal(self.parent,obj.pk,'pay')
        self.assertEqual(PointEntry.objects.count(),1)
        self.assertContains(self.client.get(reverse('points')),'-6 积分')
        self.assertContains(self.client.get(self.url),'¥ 4.00')

    def test_ledger_failure_rolls_back_payment(self):
        obj = self.apply()
        with patch('workshop.services.PointEntry.objects.create',side_effect=RuntimeError('write failed')):
            with self.assertRaises(RuntimeError):handle_withdrawal(self.parent,obj.pk,'pay')
        self.wallet.refresh_from_db(); obj.refresh_from_db()
        self.assertEqual((self.wallet.points,self.wallet.frozen_points),(10,6))
        self.assertEqual(obj.status,'pending')

    def test_http_application_uses_points_and_parent_can_confirm(self):
        response = self.client.post(self.url,{'action':'apply','amount':6,'request_key':uuid.uuid4()})
        self.assertRedirects(response,reverse('wallet'))
        obj = Withdrawal.objects.get()
        self.assertEqual((obj.points_amount,obj.cents),(6,600))
        self.assertContains(self.client.get(self.url),'¥ 4.00')
        self.client.force_login(self.parent)
        response = self.client.post(self.url,{'action':'pay','withdrawal':obj.pk})
        self.assertRedirects(response,self.url)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.points,4)

    def test_student_and_unrelated_parent_cannot_pay(self):
        obj = self.apply()
        other = User.objects.create_user('withdraw-other',is_parent=True)
        for actor in (self.child,other):
            with self.assertRaises(PermissionDenied):handle_withdrawal(actor,obj.pk,'pay')
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.points,self.wallet.frozen_points),(10,6))

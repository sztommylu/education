import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, close_old_connections
from django.test import TestCase, Client, TransactionTestCase, override_settings
from django.utils import timezone
from .models import *
from .services import *
from .resources import upload_asset
from .worker import run_ai_once, maintenance
from .views import focus_query

class BusinessTests(TestCase):
    def setUp(self):
        self.child=User.objects.create_user('student',password='Pass-test-123',first_name='小禾',is_student=True)
        self.parent=User.objects.create_user('parent',password='Pass-test-123',is_parent=True)
        self.other=User.objects.create_user('other',password='Pass-test-123',is_parent=True)
        self.admin=User.objects.create_superuser('admin',password='Pass-test-123')
        ParentChild.objects.create(parent=self.parent,child=self.child)
        self.subject=Subject.objects.create(name='数学')
        self.template=TaskTemplate.objects.create(child=self.child,subject=self.subject,title='数学探索',requirements='完成独立小题',review_mode='ai',enabled=True)
        SiteConfig.load()
    def task(self,**kwargs):
        kwargs.setdefault('template',self.template)
        return create_task(self.parent,self.child,'小目标',self.subject,requirements='完成三道题',**kwargs)
    def submitted(self,task=None):
        task=task or self.task()
        save_draft(self.child,task.pk,'完成三道题','已完成，写出了过程')
        return submit_task(self.child,task.pk)
    def reviewed(self,sub=None,grade='B'):
        sub=sub or self.submitted()
        record_review(sub.pk,'ai',{'grade':grade,'reason':'说明','suggestions':'建议','knowledge':[],'mistakes':[]},'mock')
        return sub
    def settle(self,sub,points=0):
        return finalize(self.parent,sub.task_id,sub.pk,'settle','B',points=points)
    def login(self,user):
        user.refresh_from_db()
        self.client.force_login(user)

    def test_template_and_version_snapshots(self):
        task=self.task()
        self.template.requirements='更改后的要求'; self.template.save()
        sub=self.submitted(task)
        save_draft(self.child,task.pk,'新要求','修改草稿')
        sub.refresh_from_db()
        self.assertEqual(sub.snapshot['template']['requirements'],'完成独立小题')
        self.assertEqual(sub.result,'已完成，写出了过程')
        with self.assertRaises(ValueError): sub.save()
    def test_queue_frozen_config(self):
        SubjectRule.objects.create(child=self.child,subject=self.subject,prompt='原学科规则')
        sub=self.submitted(); job=sub.reviewjob
        config=SiteConfig.load(); config.global_prompt='新规则'; config.ai_model='new'; config.save()
        SubjectRule.objects.filter(child=self.child).update(prompt='新学科规则')
        job.refresh_from_db()
        self.assertEqual(job.frozen['subject_prompt'],'原学科规则')
        self.assertNotEqual(job.frozen['model'],'new')
        self.assertIn('schema',job.frozen)
    def test_old_review_never_overwrites_current(self):
        task=self.task(); old=self.submitted(task); new=self.submitted(task)
        self.reviewed(old)
        task.refresh_from_db(); self.assertEqual(task.phase,'waiting'); self.assertEqual(task.current_id,new.pk)
    def test_settlement_once_and_closed_task(self):
        sub=self.reviewed(); self.settle(sub)
        with self.assertRaises(ValidationError): self.settle(sub)
        with self.assertRaises(ValidationError): submit_task(self.child,sub.task_id)
        self.assertEqual(Wallet.objects.get(child=self.child).points,0)
    def test_stale_version_finalization_rejected(self):
        sub=self.reviewed(); new=self.submitted(sub.task)
        with self.assertRaises(ValidationError): self.settle(sub)
        self.assertFalse(Settlement.objects.exists())
    def test_reject_is_zero_and_no_delete_after_submit(self):
        sub=self.submitted()
        finalize(self.parent,sub.task_id,sub.pk,'reject',points=100)
        self.assertEqual(Settlement.objects.get(task=sub.task).points,0)
        with self.assertRaises(ValidationError): delete_task(self.parent,sub.task_id)
    def test_return_preserves_versions_and_feedback(self):
        sub=self.reviewed()
        finalize(self.parent,sub.task_id,sub.pk,'return',note='补充过程')
        sub.task.refresh_from_db(); self.assertEqual(sub.task.phase,'revision')
        self.assertEqual(sub.task.versions.count(),1)
        self.assertEqual(FinalFeedback.objects.get(task=sub.task).note,'补充过程')
    def test_legacy_templates_do_not_generate_tasks(self):
        self.template.auto_generate=True; self.template.weekdays=list(range(7)); self.template.save()
        ensure_today(self.child); ensure_today(self.child)
        self.assertEqual(Task.objects.count(),0)
    def test_admin_not_parent_and_unrelated_parent_denied(self):
        task=self.task()
        for actor in [self.admin,self.other]:
            with self.assertRaises(PermissionDenied): require_access(actor,self.child)
            self.login(actor); self.assertEqual(self.client.get(f'/tasks/{task.pk}/').status_code,403)
    def test_student_analysis_denied_and_pending_account_denied(self):
        self.login(self.child); self.assertEqual(self.client.get('/analysis/').status_code,403)
        pending=User.objects.create_user('pending',password='Pass-test-123')
        self.login(pending); self.assertEqual(self.client.get('/').status_code,403)
    def test_student_cannot_delete_task(self):
        task=self.task()
        with self.assertRaises(PermissionDenied): delete_task(self.child,task.pk)
        delete_task(self.parent,task.pk); task.refresh_from_db(); self.assertTrue(task.deleted)

    def test_open_task_shows_deleted_message_after_refresh(self):
        task=self.task()
        self.login(self.child)
        self.assertEqual(self.client.get(f'/tasks/{task.pk}/').status_code,200)
        delete_task(self.parent,task.pk)
        response=self.client.get(f'/tasks/{task.pk}/')
        self.assertEqual(response.status_code,410)
        self.assertContains(response,'任务已被删除',status_code=410)
        self.assertContains(response,'返回任务列表',status_code=410)
        response=self.client.post(f'/tasks/{task.pk}/',{'operation':'save'},HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code,410)
        self.assertEqual(response.json(),{'error':'任务已被删除'})
    def test_role_and_identity_changes_revoke_session(self):
        self.login(self.child); self.assertEqual(self.client.get('/').status_code,200)
        self.child.is_student=False; self.child.save()
        self.assertEqual(self.client.get('/').status_code,302)
        self.child.is_student=True; self.child.save(); self.login(self.child)
        Identity.objects.create(user=self.child,issuer='https://issuer.example',subject='abc')
        self.assertEqual(self.client.get('/').status_code,302)
    def test_fixed_expiry_no_browsing_renewal(self):
        self.login(self.child)
        before=self.client.session.get_expiry_date()
        self.client.get('/'); after=self.client.session.get_expiry_date()
        self.assertEqual(before,after)
    def test_csrf_enforced(self):
        client=Client(enforce_csrf_checks=True); client.force_login(self.child)
        self.assertEqual(client.post('/wallet/',{'action':'apply','amount':'1.00','request_key':str(uuid.uuid4())}).status_code,403)
    @override_settings(MEDIA_ROOT='/tmp/growth-workshop-test-media')
    def test_private_attachment_version_retention_and_range(self):
        task=self.task()
        a=upload_asset(self.child,SimpleUploadedFile('note.txt',b'hello study'),task)
        sub=self.submitted(task)
        save_draft(self.child,task.pk,'新要求','新结果',[a.pk])
        self.assertTrue(sub.attachments.filter(pk=a.pk).exists())
        self.login(self.other); self.assertEqual(self.client.get(f'/assets/{a.pk}/').status_code,403)
        self.login(self.child)
        response=self.client.get(f'/assets/{a.pk}/',HTTP_RANGE='bytes=0-4')
        self.assertEqual(response.status_code,206)
        self.assertEqual(b''.join(response.streaming_content),b'hello')
        response=self.client.get(f'/assets/{a.pk}/',HTTP_RANGE='bytes=1000-2000'); self.assertEqual(response.status_code,416)
    def test_disguised_html_upload_rejected(self):
        task=self.task()
        with self.assertRaises(ValidationError): upload_asset(self.child,SimpleUploadedFile('nice.png',b'<!doctype html><html><script>alert(1)</script></html>'),task)
    def test_worker_mock_and_normal_grade_no_retry(self):
        sub=self.submitted(); self.assertTrue(run_ai_once()); sub.refresh_from_db()
        self.assertEqual(sub.review.grade,'B'); self.assertIn('开发模拟',sub.review.reason)
        self.assertFalse(run_ai_once()); self.assertEqual(AIRun.objects.count(),1)
    @patch('workshop.worker.generate',side_effect=PermissionError('missing key'))
    def test_ai_failure_fallback_does_not_disable_future_submissions(self,generate):
        sub=self.submitted(); run_ai_once()
        self.assertEqual(ReviewJob.objects.get(submission=sub).status,'parent')
        new=self.submitted(sub.task)
        self.assertEqual(ReviewJob.objects.get(submission=new).status,'pending')
    def test_pages_render_with_live_data(self):
        sub=self.reviewed(); self.settle(sub)
        for actor,paths in [(self.child,['/','/tasks/','/tasks/?view=archive',f'/tasks/{sub.task_id}/','/wallet/','/records/','/password/']),(self.parent,['/tasks/new/','/manage/','/manage/rules/','/benefits/','/analysis/','/wallet/',f'/tasks/{sub.task_id}/']),(self.admin,['/admin/','/site-settings/'])]:
            self.login(actor)
            for path in paths:
                with self.subTest(actor=actor.username,path=path): self.assertEqual(self.client.get(path).status_code,200)
        self.login(self.parent)
        self.assertEqual(self.client.get('/manage/milestones/').status_code,403)
        self.assertEqual(self.client.get('/honor/').status_code,404)
    @override_settings(MEDIA_ROOT='/tmp/growth-workshop-test-media')
    def test_http_create_submit_review_finalize_flow(self):
        self.login(self.child)
        task=create_task(self.parent,self.child,'真实流程',self.subject,requirements='写三题')
        self.assertEqual(self.client.post(f'/tasks/{task.pk}/',{'operation':'submit','attachment':SimpleUploadedFile('answers.txt',b'Answers and work shown.')} ).status_code,302)
        run_ai_once(); task.refresh_from_db()
        self.login(self.parent)
        response=self.client.post(f'/tasks/{task.pk}/decision/',{'version':task.current_id,'decision':'complete'})
        self.assertEqual(response.status_code,302); task.refresh_from_db(); self.assertEqual(task.phase,'awarded')
    def test_focus_archive_boundaries(self):
        sub=self.reviewed(); self.settle(sub)
        task=sub.task
        self.assertTrue(focus_query(self.child).filter(pk=task.pk).exists())
        Task.objects.filter(pk=task.pk).update(day=today()-timedelta(days=1))
        self.assertTrue(focus_query(self.child,True).filter(pk=task.pk).exists())
        long=self.task(kind='long'); longsub=self.reviewed(self.submitted(long)); self.settle(longsub)
        self.assertTrue(focus_query(self.child,True).filter(pk=long.pk).exists())
        Task.objects.filter(pk=long.pk).update(due_date=today())
        self.assertTrue(focus_query(self.child).filter(pk=long.pk).exists())

    def test_manual_parent_review_then_final_settlement(self):
        task=self.task(); task.template_snapshot['review_mode']='parent'; task.save()
        sub=self.submitted(task)
        parent_review(self.parent,task.pk,sub.pk,'C','主要要求已完成')
        self.settle(sub)
        self.assertEqual(Settlement.objects.get(task=task).grade,'B')
    def test_pending_ai_cannot_be_manually_overwritten(self):
        sub=self.submitted()
        with self.assertRaises(ValidationError): parent_review(self.parent,sub.task_id,sub.pk,'A','说明')
        self.assertFalse(Review.objects.exists())
    def test_ai_finishing_after_return_does_not_override_revision(self):
        sub=self.submitted()
        finalize(self.parent,sub.task_id,sub.pk,'return',note='先补充材料')
        self.reviewed(sub)
        sub.task.refresh_from_db()
        self.assertEqual(sub.task.phase,'revision')
    def test_notifications_are_only_acknowledged_explicitly(self):
        n=Notification.objects.create(child=self.child,kind='points',title='积分到账')
        self.login(self.child)
        self.client.get('/tasks/'); self.client.get('/')
        n.refresh_from_db(); self.assertIsNone(n.acknowledged_at)
        self.client.post(f'/notifications/{n.pk}/ack/')
        n.refresh_from_db(); self.assertIsNotNone(n.acknowledged_at)
    def test_analysis_outputs_are_escaped(self):
        sub=self.submitted()
        record_review(sub.pk,'ai',{'grade':'B','reason':'<script>alert(1)</script>','suggestions':'<img onerror=alert(1)>','knowledge':[{'topic':'<script>x</script>','observation':'<iframe>bad</iframe>'}],'mistakes':[]})
        self.login(self.child)
        response=self.client.get(f'/tasks/{sub.task_id}/')
        self.assertEqual(sub.review.reason,'<script>alert(1)</script>')
        self.assertNotContains(response,'<script>alert(1)</script>')
        self.login(self.parent)
        self.assertContains(self.client.get('/analysis/'),'&lt;iframe&gt;bad&lt;/iframe&gt;')
    def test_password_changes_revoke_all_student_sessions(self):
        self.login(self.child)
        second=Client(); second.force_login(self.child)
        self.child.set_password('A-New-Password-123'); self.child.save()
        self.assertEqual(self.client.get('/').status_code,302)
        self.assertEqual(second.get('/').status_code,302)
    def test_identity_reassignment_revokes_both_users(self):
        second=User.objects.create_user('secondstudent',password='Pass-test-123',is_student=True)
        identity=Identity.objects.create(user=self.child,issuer='https://issuer.example',subject='external')
        self.login(self.child)
        second.refresh_from_db(); otherclient=Client(); otherclient.force_login(second)
        identity.user=second; identity.save()
        self.assertEqual(self.client.get('/').status_code,302)
        self.assertEqual(otherclient.get('/').status_code,302)
    def test_draft_removal_cannot_remove_other_tasks_files(self):
        a=self.task(); b=self.task()
        with override_settings(MEDIA_ROOT='/tmp/growth-workshop-test-media'):
            file=upload_asset(self.child,SimpleUploadedFile('example.txt',b'hello'),b)
            save_draft(self.child,a.pk,'req','result',[file.pk])
        file.refresh_from_db(); self.assertTrue(file.draft_active)
    def test_invalid_ai_schema_is_logged_and_handed_off(self):
        from .ai import InvalidOutput
        sub=self.submitted()
        with patch('workshop.worker.generate',side_effect=InvalidOutput('schema error',{'invalid':'payload'},'actual-provider-model')):
            run_ai_once()
        run=AIRun.objects.get(); self.assertIn('payload',run.response)
        self.assertEqual(run.actual_model,'actual-provider-model')
        self.assertEqual(ReviewJob.objects.get(submission=sub).status,'parent')
        self.assertFalse(Review.objects.exists())
    def test_network_retry_budget_is_finite_and_persistent(self):
        import httpx
        sub=self.submitted()
        with patch('workshop.worker.generate',side_effect=httpx.ConnectError('offline')) as generate:
            for i in range(3):
                ReviewJob.objects.filter(submission=sub).update(next_at=timezone.now()-timedelta(seconds=1))
                self.assertTrue(run_ai_once())
            self.assertFalse(run_ai_once()); self.assertEqual(generate.call_count,3)
        job=ReviewJob.objects.get(submission=sub)
        self.assertEqual((job.status,job.attempts),('parent',3))
    def test_resource_cleanup_keeps_formal_versions(self):
        from .resources import cleanup_resources
        with override_settings(MEDIA_ROOT='/tmp/growth-workshop-test-media'):
            task=self.task()
            asset=upload_asset(self.child,SimpleUploadedFile('retained.txt',b'keep forever'),task)
            self.submitted(task)
            save_draft(self.child,task.pk,'req','changed',[asset.pk])
            Asset.objects.filter(pk=asset.pk).update(created_at=timezone.now()-timedelta(hours=2))
            cleanup_resources()
            self.assertTrue(Asset.objects.filter(pk=asset.pk).exists())
            self.assertTrue(asset.file.storage.exists(asset.file.name))

from unittest import skipUnless
@skipUnless(connection.vendor=='postgresql','需要 PostgreSQL 才能验证真实行锁并发')
class PostgreSQLConcurrencyTests(TransactionTestCase):
    reset_sequences=True
    def setUp(self):
        self.child=User.objects.create_user('concurrent-child',is_student=True)
        self.parent=User.objects.create_user('concurrent-parent',is_parent=True)
        ParentChild.objects.create(parent=self.parent,child=self.child)
        self.subject=Subject.objects.create(name='数学')
        Wallet.objects.create(child=self.child,points=1)
        SiteConfig.load()
    def test_concurrent_withdrawals_never_overdraw(self):
        from threading import Barrier
        barrier=Barrier(2)
        def apply():
            close_old_connections()
            actor=User.objects.get(pk=self.child.pk)
            barrier.wait()
            try:
                request_withdrawal(actor,100,uuid.uuid4()); return 'accepted'
            except ValidationError: return 'insufficient'
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool: result=list(pool.map(lambda _:apply(),range(2)))
        self.assertCountEqual(result,['accepted','insufficient'])
        wallet=Wallet.objects.get(child=self.child)
        self.assertEqual((wallet.points,wallet.frozen_points),(1,1))
    def test_concurrent_finalization_settles_once(self):
        from threading import Barrier
        task=create_task(self.parent,self.child,'并发任务',self.subject)
        save_draft(self.child,task.pk,'要求','完成')
        sub=submit_task(self.child,task.pk)
        record_review(sub.pk,'parent',{'grade':'B','reason':'完成','knowledge':[],'mistakes':[]})
        barrier=Barrier(2)
        def settle():
            close_old_connections()
            actor=User.objects.get(pk=self.parent.pk)
            barrier.wait()
            try:
                finalize(actor,task.pk,sub.pk,'settle','B'); return 'settled'
            except ValidationError: return 'closed'
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool: result=list(pool.map(lambda _:settle(),range(2)))
        self.assertCountEqual(result,['settled','closed'])
        self.assertEqual(Settlement.objects.count(),1)
        self.assertEqual(Wallet.objects.get(child=self.child).points,0)

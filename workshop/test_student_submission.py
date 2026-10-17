from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import ParentChild, Subject, User
from .services import create_task, save_draft, submit_task


class StudentAttachmentSubmissionTests(TestCase):
    def setUp(self):
        media = TemporaryDirectory(prefix='growth-student-upload-')
        self.addCleanup(media.cleanup)
        settings = override_settings(MEDIA_ROOT=media.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.child = User.objects.create_user('attachment-student', is_student=True)
        self.parent = User.objects.create_user('attachment-parent', is_parent=True)
        ParentChild.objects.create(parent=self.parent, child=self.child)
        self.subject = Subject.objects.create(name='英语')
        self.task = create_task(self.parent, self.child, '单词默写', self.subject,
                                requirements='请默写五个单词，拍照提交。')
        self.url = reverse('task_detail', args=[self.task.pk])
        self.client.force_login(self.child)

    def attachment(self):
        return SimpleUploadedFile('answer.txt', b'apple, book, garden, school, practice')

    def test_student_creation_entry_points_are_removed_on_empty_and_populated_pages(self):
        for path in [reverse('home'), reverse('tasks'), reverse('tasks') + '?view=archive', self.url]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, reverse('task_create'))
                self.assertNotContains(response, '创建任务')
                self.assertNotContains(response, '添加一个成长任务')

    def test_student_cannot_create_by_direct_get_or_post(self):
        count = self.child.task_set.count()
        self.assertEqual(self.client.get(reverse('task_create')).status_code, 403)
        response = self.client.post(reverse('task_create'), {
            'subject':self.subject.pk, 'title':'不能自建的任务', 'kind':'daily',
            'english_category':'vocabulary', 'words':'1. apple##苹果##', 'child':self.child.pk,
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.child.task_set.count(), count)

    def test_student_creation_is_also_blocked_in_business_service(self):
        with self.assertRaises(PermissionDenied):
            create_task(self.child, self.child, '不允许创建', self.subject)
        self.assertEqual(self.child.task_set.count(), 1)

    def test_student_sees_upload_and_two_actions_without_draft_editing_fields(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="attachment"')
        self.assertContains(response, '保存草稿')
        self.assertContains(response, '正式提交')
        self.assertNotContains(response, '编辑草稿与提交')
        for field in ('requirements', 'result', 'purpose', 'remove'):
            self.assertNotContains(response, f'name="{field}"')
        self.assertContains(response, '请默写五个单词，拍照提交。')

    def test_saving_upload_preserves_existing_text_and_does_not_formally_submit(self):
        save_draft(self.child, self.task.pk, self.task.requirements, '以前填写的成果')
        response = self.client.post(self.url, {'operation':'save', 'attachment':self.attachment(),
            'requirements':'伪造要求', 'result':'覆盖旧成果', 'purpose':'requirements'})
        self.assertRedirects(response, self.url)
        self.task.refresh_from_db()
        self.assertEqual(self.task.requirements, '请默写五个单词，拍照提交。')
        self.assertEqual(self.task.result, '以前填写的成果')
        self.assertIsNone(self.task.current_id)
        self.assertEqual(self.task.assets.get().purpose, 'result')

    def test_uploaded_attachment_can_be_saved_then_formally_submitted(self):
        self.client.post(self.url, {'operation':'save', 'attachment':self.attachment()})
        response = self.client.post(self.url, {'operation':'submit'})
        self.assertRedirects(response, reverse('tasks'))
        self.task.refresh_from_db()
        self.assertEqual(self.task.phase, 'waiting')
        self.assertEqual(self.task.current.requirements, '请默写五个单词，拍照提交。')
        self.assertEqual(self.task.current.result, '')
        self.assertEqual(self.task.current.attachments.count(), 1)

    def test_upload_and_formal_submission_can_be_one_action(self):
        response = self.client.post(self.url, {'operation':'submit', 'attachment':self.attachment()})
        self.assertEqual(response.status_code, 302)
        self.task.refresh_from_db()
        self.assertEqual(self.task.current.attachments.count(), 1)

    def test_empty_submission_and_forged_text_only_result_are_rejected(self):
        response = self.client.post(self.url, {'operation':'submit', 'result':'伪造成果'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '上传成果附件')
        self.task.refresh_from_db()
        self.assertIsNone(self.task.current_id)
        self.assertEqual(self.task.result, '')

    def test_resubmitting_preserves_previous_formal_attachment_references(self):
        self.client.post(self.url, {'operation':'submit', 'attachment':self.attachment()})
        self.task.refresh_from_db()
        old = self.task.current
        old_ids = set(old.attachments.values_list('pk', flat=True))
        self.client.post(self.url, {'operation':'submit', 'attachment':self.attachment()})
        self.task.refresh_from_db()
        self.assertEqual(self.task.current.number, 2)
        self.assertEqual(set(old.attachments.values_list('pk', flat=True)), old_ids)
        self.assertEqual(self.task.current.attachments.count(), 2)

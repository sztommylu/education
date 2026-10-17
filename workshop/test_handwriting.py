import json
from copy import deepcopy
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from .ai import build_input, generate, gemini_output_schema
from .dictation import parse_strokes
from .forms import ParentTaskForm
from .models import User, Subject, ParentChild, ReviewJob, Review, SiteConfig
from .services import create_task
from .worker import run_ai_once


class StrokeValidationTests(SimpleTestCase):
    def test_invalid_shapes_numbers_limits_and_empty_answers(self):
        cases = ['{}', '[]', '[{"strokes":[]}]',
                 '[{"strokes":[{"width":5,"points":[[NaN,1]]}]}]',
                 '[{"strokes":[{"width":5,"points":[[0,401]]}]}]',
                 '[{"strokes":[{"width":100,"points":[[0,1]]}]}]',
                 '[{"strokes":[],"recognized":"forged"}]']
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                parse_strokes(payload, 1)


class HandwritingSubmissionTests(TestCase):
    def setUp(self):
        self.parent = User.objects.create_user('dictation-parent', is_parent=True)
        self.child = User.objects.create_user('dictation-child', is_student=True)
        ParentChild.objects.create(parent=self.parent, child=self.child)
        self.subject = Subject.objects.create(name='英语')
        self.assignment = {'version':1,'course':'英语','category':'vocabulary','category_label':'字词',
                           'content':{'words':'apple:苹果\nbook：书','phrases':'','sentences':''}}
        self.task = create_task(self.parent, self.child, '手写单词', self.subject, assignment=self.assignment)
        self.url = reverse('task_detail', args=[self.task.pk])
        self.rows = [{'strokes':[{'width':5,'points':[[30,50],[100,80]]}]}, {'strokes':[]}]
        self.payload = {'operation':'save_handwriting','handwriting':json.dumps(self.rows), 'result':'forged correct answer'}
        self.client.force_login(self.child)

    def submit(self, recognized='aple'):
        with patch('workshop.dictation.recognize_strokes', return_value={'text':recognized,'confidence':.82}):
            return self.client.post(self.url, self.payload)

    def test_only_chinese_and_handwriting_controls_are_rendered(self):
        response = self.client.get(self.url)
        self.assertContains(response, '任务要求')
        self.assertContains(response, '提交 AI 批改')
        self.assertContains(response, '苹果')
        self.assertNotContains(response, 'apple')
        self.assertNotContains(response, 'name="attachment"')
        self.assertNotContains(response, 'name="result"')
        self.assertNotContains(response, '保存草稿')

    def test_saving_recognizes_and_queues_ai_without_sending_pen_strokes(self):
        self.assertRedirects(self.submit(), self.url)
        self.task.refresh_from_db()
        self.assertEqual(self.task.handwriting['rows'], self.rows)
        self.assertIn('苹果：aple', self.task.result)
        self.assertNotIn('forged', self.task.result)
        sub = self.task.current
        self.assertEqual(sub.snapshot['dictation']['answers'][0]['recognized'], 'aple')
        self.assertEqual(sub.attachments.count(), 0)
        job = ReviewJob.objects.get(submission=sub)
        data = build_input(job)
        self.assertEqual(data['answers'][0]['expected'], 'apple')
        self.assertEqual(data['answers'][1]['recognized'], '')
        for key in ['rows', 'strokes', 'points', 'context', 'template_requirements']:
            self.assertNotIn(key, json.dumps(data))
        response = self.client.get(self.url)
        self.assertContains(response, 'AI 正在批改')
        self.assertNotContains(response, '"expected"')
        self.assertNotContains(response, 'apple')

    def test_old_form_actions_cannot_bypass_handwriting_and_upload_is_rejected(self):
        for operation in ['submit', 'save']:
            response = self.client.post(self.url, {'operation':operation, 'result':'apple'})
            self.assertEqual(response.status_code, 200)
        response = self.client.post(self.url, {**self.payload, 'attachment':SimpleUploadedFile('answer.txt', b'apple')})
        self.assertContains(response, '无需上传附件')
        self.assertFalse(ReviewJob.objects.exists())

    def test_failed_ocr_keeps_pen_strokes_visible_without_partial_submission(self):
        with patch('workshop.dictation.recognize_strokes', side_effect=RuntimeError('model unavailable')):
            response = self.client.post(self.url, self.payload)
        self.assertContains(response, '本地 OCR 暂不可用')
        self.assertEqual(response.context['handwriting_rows'], self.rows)
        self.task.refresh_from_db()
        self.assertIsNone(self.task.current_id)
        self.assertEqual(self.task.handwriting, {})

    def test_cross_student_access_is_denied(self):
        self.client.force_login(User.objects.create_user('stranger', is_student=True))
        with patch('workshop.dictation.recognize_strokes') as ocr:
            self.assertEqual(self.client.post(self.url, self.payload).status_code, 403)
            ocr.assert_not_called()

    def test_version_retains_original_ink_and_pending_job_blocks_duplicates(self):
        self.submit()
        self.task.refresh_from_db()
        old = self.task.current
        snapshot = deepcopy(old.snapshot)
        self.assertContains(self.submit(), '本次默写正在批改')
        self.assertEqual(self.task.versions.count(), 1)
        ReviewJob.objects.filter(submission=old).update(status='done')
        self.submit('apple')
        old.refresh_from_db()
        self.assertEqual(old.snapshot, snapshot)
        self.task.refresh_from_db()
        self.assertEqual(self.task.current.number, 2)
        self.assertEqual(self.task.current.snapshot['dictation']['answers'][0]['recognized'], 'apple')

    def test_worker_persists_ai_feedback_without_history_panel(self):
        self.submit()
        output = {'grade':'C','reason':'第 1 题拼写错误，第 2 题未作答。','suggestions':'核对漏写的 p。','knowledge':[],
                  'mistakes':[{'topic':'apple 拼写','evidence':'苹果','student_answer':'aple',
                               'reference_answer':'apple','explanation':'漏写了一个 p。'}]}
        with patch('workshop.worker.generate', return_value=(output,'test-model',{},{})):
            self.assertTrue(run_ai_once())
        self.assertEqual(Review.objects.get().actual_model, 'test-model')
        response = self.client.get(self.url)
        self.assertEqual(Review.objects.get().output['mistakes'][0]['explanation'], '漏写了一个 p。')
        self.assertNotContains(response, '成长足迹')
        self.assertNotContains(response, 'data-handwriting-pending')

    def test_real_adapter_receives_only_text(self):
        self.submit()
        job = ReviewJob.objects.get()
        job.frozen.update(adapter='deepseek', url='https://ai.example.test', model='test-model')
        result = {'grade':'C','reason':'拼写有误','suggestions':'重新默写','knowledge':[],'mistakes':[]}
        with self.settings(AI_API_KEY='test-key'), patch('workshop.ai.httpx.post') as post:
            post.return_value.json.return_value = {'choices':[{'message':{'content':json.dumps(result)}}]}
            generate(job)
        body = post.call_args.kwargs['json']
        content = body['messages'][1]['content']
        self.assertIsInstance(content, str)
        self.assertNotIn('points', content)
        self.assertIn('aple', content)

    def test_new_assignments_require_chinese_prompts(self):
        form = ParentTaskForm({'subject':self.subject.pk,'kind':'daily','english_category':'vocabulary','words':'apple'})
        self.assertFalse(form.is_valid())
        self.assertIn('words', form.errors)

    def test_gemini_receives_text_and_schema_with_key_in_header(self):
        self.submit()
        job = ReviewJob.objects.get()
        job.frozen.update(adapter='gemini', url='https://generativelanguage.googleapis.com/v1beta', model='gemini-2.5-flash')
        result = {'grade':'C','reason':'拼写有误','suggestions':'重新默写','knowledge':[],'mistakes':[]}
        with self.settings(AI_API_KEY='test-key'), patch('workshop.ai.httpx.post') as post:
            post.return_value.json.return_value = {'candidates':[{'content':{'parts':[{'text':json.dumps(result)}]}}]}
            output, _, _, _ = generate(job)
        self.assertEqual(output, result)
        self.assertEqual(post.call_args.args[0], 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent')
        self.assertEqual(post.call_args.kwargs['headers'], {'x-goog-api-key':'test-key'})
        body = post.call_args.kwargs['json']
        self.assertEqual(body['generationConfig']['responseJsonSchema'], gemini_output_schema(job.frozen['schema']))
        parts = body['contents'][0]['parts']
        self.assertEqual(len(parts), 1)
        self.assertIn('aple', parts[0]['text'])
        self.assertNotIn('strokes', parts[0]['text'])

    def test_missing_gemini_key_does_not_send_request(self):
        self.submit()
        job = ReviewJob.objects.get()
        job.frozen['adapter'] = 'gemini'
        with self.settings(AI_API_KEY=''), patch('workshop.ai.httpx.post') as post:
            with self.assertRaises(PermissionError):
                generate(job)
            post.assert_not_called()

    def save_one(self, index=0, revision=0, strokes=None):
        return self.client.post(self.url, {
            'operation':'save_handwriting_answer', 'index':index, 'revision':revision,
            'handwriting':json.dumps([{'strokes':strokes or self.rows[0]['strokes']}]),
        })

    def test_category_start_buttons_and_only_one_writing_canvas(self):
        response = self.client.get(self.url)
        for label in ['单词', '词组', '句子']:
            self.assertContains(response, f'data-start-category="{label}"')
        self.assertContains(response, 'id="handwriting-canvas"', count=1)
        self.assertContains(response, '保存本题')
        self.assertContains(response, '下一题')
        self.assertContains(response, 'data-start-category="词组" disabled')

    def test_save_one_is_durable_without_ocr_submission_or_ai_call(self):
        with patch('workshop.dictation.recognize_strokes') as ocr:
            response = self.save_one()
            self.assertEqual(response.status_code, 200)
            ocr.assert_not_called()
        self.assertEqual(response.json(), {'revision':1,'saved_indices':[0]})
        self.task.refresh_from_db()
        self.assertIsNone(self.task.current_id)
        self.assertFalse(ReviewJob.objects.exists())
        state = self.client.get(self.url).context['handwriting_state']
        self.assertEqual(state['rows'][0], self.rows[0])
        self.assertEqual(state['saved_indices'], [0])
        self.assertEqual(self.task.handwriting, {})
        self.assertEqual(self.task.result, '')

    def test_incomplete_draft_cannot_be_submitted(self):
        self.save_one()
        with patch('workshop.dictation.recognize_strokes') as ocr:
            response = self.client.post(self.url, {'operation':'submit_handwriting_draft', 'revision':1})
            ocr.assert_not_called()
        self.assertContains(response, '请先完成并保存所有单词、词组和句子')
        self.assertFalse(ReviewJob.objects.exists())

    def test_complete_draft_submits_saved_rows_and_retains_old_versions(self):
        self.save_one(0, 0)
        self.save_one(1, 1)
        with patch('workshop.dictation.recognize_strokes', return_value={'text':'aple','confidence':.8}) as ocr:
            response = self.client.post(self.url, {'operation':'submit_handwriting_draft','revision':2,'handwriting':'forged'})
        self.assertRedirects(response, self.url)
        self.assertEqual(ocr.call_count, 2)
        self.task.refresh_from_db()
        self.assertEqual(self.task.current.snapshot['dictation']['rows'], [self.rows[0],self.rows[0]])
        old = self.task.current
        snapshot = deepcopy(old.snapshot)
        ReviewJob.objects.filter(submission=old).update(status='parent')
        new_strokes = [{'width':5,'points':[[50,50],[150,100]]}]
        self.assertEqual(self.save_one(0, 2, new_strokes).status_code, 200)
        old.refresh_from_db()
        self.assertEqual(old.snapshot, snapshot)
        self.task.refresh_from_db()
        self.assertEqual(self.task.handwriting['rows'][0], self.rows[0])
        self.assertEqual(self.task.handwriting_draft['rows'][0]['strokes'], new_strokes)

    def test_failed_save_and_stale_tabs_do_not_overwrite_progress(self):
        self.save_one()
        before = self.client.get(self.url).context['handwriting_state']
        for data in [
            {'index':1,'revision':0,'handwriting':json.dumps([self.rows[0]])},
            {'index':99,'revision':1,'handwriting':json.dumps([self.rows[0]])},
            {'index':1,'revision':1,'handwriting':'[{"strokes":[]}]'},
            {'index':'invalid','revision':1,'handwriting':json.dumps([self.rows[0]])},
        ]:
            response = self.client.post(self.url, {'operation':'save_handwriting_answer', **data})
            self.assertEqual(response.status_code, 400)
            self.assertIn('error', response.json())
        self.assertEqual(self.client.get(self.url).context['handwriting_state'], before)

    def test_ocr_failure_keeps_saved_draft_for_retry(self):
        self.save_one(0, 0)
        self.save_one(1, 1)
        with patch('workshop.dictation.recognize_strokes', side_effect=RuntimeError('unavailable')):
            response = self.client.post(self.url, {'operation':'submit_handwriting_draft','revision':2})
        self.assertContains(response, '本地 OCR 暂不可用')
        self.assertEqual(response.context['handwriting_state']['saved_indices'], [0,1])
        self.assertFalse(ReviewJob.objects.exists())

    def test_question_saving_rejects_other_students_parents_closed_and_pending_tasks(self):
        other = User.objects.create_user('stepwise-stranger', is_student=True)
        for actor in [other, self.parent]:
            actor.refresh_from_db()
            self.client.force_login(actor)
            self.assertEqual(self.save_one().status_code, 403)
        self.client.force_login(self.child)
        self.task.phase = 'awarded'
        self.task.save(update_fields=['phase'])
        self.assertEqual(self.save_one().status_code, 400)
        self.task.phase = 'draft'
        self.task.save(update_fields=['phase'])
        self.submit()
        self.assertEqual(self.save_one().status_code, 400)

    def test_parent_vocabulary_detail_has_no_draft_or_upload_form(self):
        self.parent.refresh_from_db()
        self.client.force_login(self.parent)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'apple:苹果')
        self.assertContains(response, '当前完成结果')
        for text in ['完成结果与学习心得', '上传附件（每次一份，最多 50 MB）',
                     '附件用途', '从当前草稿移除附件', '保存草稿', '正式提交 ↗', '编辑草稿与提交']:
            self.assertNotContains(response, text)
        for field in ['result', 'attachment', 'purpose', 'remove']:
            self.assertNotContains(response, f'name="{field}"')
        for operation in ['save', 'submit']:
            response = self.client.post(self.url, {'operation':operation, 'result':'替学生提交', 'purpose':'result'})
            self.assertEqual(response.status_code, 403)
        self.task.refresh_from_db()
        self.assertEqual(self.task.result, '')
        self.assertIsNone(self.task.current_id)

    def test_parent_vocabulary_detail_has_no_review_controls(self):
        self.submit()
        self.task.refresh_from_db()
        ReviewJob.objects.filter(submission=self.task.current).update(status='parent')
        self.parent.refresh_from_db()
        self.client.force_login(self.parent)
        response = self.client.get(self.url)
        self.assertNotContains(response, '家长初审')
        self.assertNotContains(response, '家长最终复核')
        self.assertContains(response, '识别原文：aple')
        self.assertNotContains(response, 'name="attachment"')
        self.assertNotContains(response, '保存草稿')

    def parent_detail(self, query=''):
        self.parent.refresh_from_db()
        self.client.force_login(self.parent)
        return self.client.get(self.url + query)

    def test_parent_completion_shows_partial_saved_student_work(self):
        self.save_one()
        response = self.parent_detail()
        completion = response.context['completion']
        self.assertEqual((completion['saved'], completion['total']), (1,2))
        self.assertEqual(completion['status'], '正在完成，尚未提交批改')
        self.assertContains(response, '已保存 1 / 2 题')
        first, second = completion['sections'][0]['items']
        self.assertTrue(first['saved'])
        self.assertEqual(first['strokes'], self.rows[0]['strokes'])
        self.assertIsNone(first['recognized'])
        self.assertFalse(second['saved'])
        self.assertContains(response, '学生尚未保存本题答案')
        self.assertContains(response, '提交批改后显示识别结果')
        self.assertNotContains(response, 'name="attachment"')
        self.assertNotContains(response, '保存草稿')

    def test_parent_completion_distinguishes_not_started_and_ready_to_submit(self):
        response = self.parent_detail()
        self.assertEqual(response.context['completion']['status'], '尚未开始')
        self.client.force_login(self.child)
        self.save_one(0, 0)
        self.save_one(1, 1)
        response = self.parent_detail()
        self.assertEqual(response.context['completion']['status'], '手写已完成，待提交批改')
        self.assertEqual(response.context['completion']['saved'], 2)
        self.assertFalse(response.context['completion']['submitted'])

    def test_rewritten_answer_does_not_show_previous_recognition_or_grade_as_current(self):
        self.save_one(0, 0)
        self.save_one(1, 1)
        with patch('workshop.dictation.recognize_strokes', return_value={'text':'aple','confidence':.8}):
            self.client.post(self.url, {'operation':'submit_handwriting_draft','revision':2})
        self.task.refresh_from_db()
        ReviewJob.objects.filter(submission=self.task.current).update(status='parent')
        response = self.parent_detail()
        self.assertTrue(response.context['completion']['submitted'])
        self.assertEqual(response.context['completion']['sections'][0]['items'][0]['recognized'], 'aple')
        self.client.force_login(self.child)
        self.save_one(0, 2, [{'width':5,'points':[[150,50],[300,100]]}])
        response = self.parent_detail()
        completion = response.context['completion']
        self.assertFalse(completion['submitted'])
        self.assertIsNone(completion['sections'][0]['items'][0]['recognized'])
        formal = self.parent_detail('?review=1')
        self.assertRedirects(formal, self.url)
        self.task.current.refresh_from_db()
        self.assertEqual(self.task.current.snapshot['dictation']['rows'][0], self.rows[0])

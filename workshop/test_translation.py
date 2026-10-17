import json
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from .ai import build_input
from .dictation import questions
from .models import ParentChild, ReviewJob, Subject, User, Wallet
from .services import create_task, decide_task
from .translation import source_questions
from .worker import run_ai_once


class TranslationTaskTests(TestCase):
    numbered_source='''1. 米莉和丹尼尔正在谈论他们喜欢什么食物。两人一组，讨论你喜欢吃什么食物。以以下对话为范本。
2. 米莉：我每天都喝牛奶。你呢，丹尼尔？
3. 丹尼尔：我也是。我也喜欢鸡蛋。经常喝牛奶和吃鸡蛋对我们健康有益。它们对我们很好。
4. 提示：我们用“Me too”来表示我们与其他人有相同的感受或看法。
5. 米莉：是的。我每天早晨吃一个鸡蛋。我也喜欢鱼。鱼对我们的健康有好处。
6. 丹尼尔：我喜欢肉。它很美味。
7. 米莉：你还经常吃什么？
8. 丹尼尔：胡萝卜。它们对我们的眼睛极好！
9. 你还知道哪些其他食物？把它们写在A部分的表格里。'''

    def setUp(self):
        self.parent=User.objects.create_user('translation-parent',is_parent=True)
        self.child=User.objects.create_user('translation-child',is_student=True)
        ParentChild.objects.create(parent=self.parent,child=self.child)
        self.parent.refresh_from_db()
        self.subject=Subject.objects.create(name='英语')
        assignment={'version':1,'course':'英语','category':'translation','category_label':'课本翻译',
                    'content':{'textbook_text':self.numbered_source}}
        self.task=create_task(self.parent,self.child,'课本翻译练习',self.subject,assignment=assignment)
        self.url=reverse('task_detail',args=[self.task.pk])
        self.client.force_login(self.child)
        self.row={'strokes':[{'width':5,'points':[[50,50],[100,100]]}]}

    def save_answer(self,index,revision):
        return self.client.post(self.url,{'operation':'save_handwriting_answer','index':index,
            'revision':revision,'handwriting':json.dumps([self.row])})

    def complete_and_submit(self,count=9,start_revision=0):
        revision=start_revision
        for index in range(count):
            response=self.save_answer(index,revision)
            self.assertEqual(response.status_code,200,response.content)
            revision=response.json()['revision']
        recognized=[{'text':f'English answer {index + 1}','confidence':.9} for index in range(count)]
        with patch('workshop.dictation.recognize_strokes',side_effect=recognized):
            response=self.client.post(self.url,{'operation':'submit_handwriting_draft','revision':revision})
        self.assertRedirects(response,self.url)
        self.task.refresh_from_db()
        return revision

    def test_numbered_content_is_nine_separate_handwriting_questions(self):
        self.assertEqual(len(source_questions(self.numbered_source)),9)
        self.assertEqual(len(questions(self.task)),9)
        response=self.client.get(self.url)
        self.assertContains(response,'课本翻译 · 共 9 题')
        self.assertContains(response,'开始手写')
        self.assertContains(response,'id="handwriting-canvas"')
        self.assertContains(response,'id="handwriting-save"')
        self.assertContains(response,'id="handwriting-submit"')
        self.assertContains(response,'data-outside-task-card')
        self.assertContains(response,'米莉和丹尼尔正在谈论')
        self.assertContains(response,'把它们写在A部分的表格里')
        self.assertNotContains(response,'name="translation"')
        self.assertNotContains(response,'上传附件')
        html=response.content.decode()
        self.assertLess(html.index('id="handwriting-runner"'),html.index('id="handwriting-submit"'))

    def test_hash_textbook_format_shows_only_chinese_and_keeps_english_reference(self):
        assignment={'version':1,'course':'英语','category':'translation','category_label':'课本翻译',
                    'content':{'textbook_text':
                        'Millie: I drink milk every day. How about you, Daniel? ##米莉：我每天都喝牛奶。你呢，丹尼尔？##'}}
        task=create_task(self.parent,self.child,'新格式翻译',self.subject,assignment=assignment)
        rows=questions(task)
        self.assertEqual(rows,[{'expected':'Millie: I drink milk every day. How about you, Daniel?',
            'prompt':'米莉：我每天都喝牛奶。你呢，丹尼尔？','label':'课本翻译'}])
        response=self.client.get(reverse('task_detail',args=[task.pk]))
        self.assertContains(response,'米莉：我每天都喝牛奶。你呢，丹尼尔？')
        self.assertNotContains(response,'Millie: I drink milk every day.')

    def test_each_answer_is_saved_before_all_are_submitted_to_ai(self):
        response=self.client.post(self.url,{'operation':'submit_handwriting_draft','revision':0})
        self.assertContains(response,'请先完成并保存所有题目')
        self.assertFalse(ReviewJob.objects.exists())
        self.complete_and_submit()
        self.assertEqual(self.task.phase,'waiting')
        self.assertEqual(self.task.result.count('\n'),8)
        self.assertIn('1. English answer 1',self.task.result)
        self.assertIn('9. English answer 9',self.task.result)
        job=ReviewJob.objects.get()
        self.assertEqual(len(job.submission.snapshot['dictation']['answers']),9)
        data=build_input(job)
        self.assertEqual(data['task_type'],'英语课本翻译')
        self.assertEqual(len(data['answers']),9)
        self.assertEqual(data['answers'][0]['prompt'],source_questions(self.numbered_source)[0])
        self.assertEqual(data['answers'][-1]['recognized'],'English answer 9')

    def test_parent_sees_saved_original_handwriting_and_progress(self):
        self.assertEqual(self.save_answer(0,0).status_code,200)
        self.client.force_login(self.parent)
        response=self.client.get(self.url)
        self.assertEqual(response.context['completion']['status'],'正在完成，尚未提交批改')
        self.assertEqual(response.context['completion']['saved'],1)
        self.assertContains(response,'学生已保存的笔迹')
        self.assertNotContains(response,'id="handwriting-canvas"')

    def test_stale_invalid_and_unrelated_saves_are_rejected(self):
        self.assertEqual(self.save_answer(0,0).status_code,200)
        self.assertEqual(self.save_answer(1,0).status_code,400)
        response=self.client.post(self.url,{'operation':'save_handwriting_answer','index':99,
            'revision':1,'handwriting':json.dumps([self.row])})
        self.assertEqual(response.status_code,400)
        other=User.objects.create_user('translation-other',is_student=True)
        self.client.force_login(other)
        self.assertEqual(self.save_answer(1,1).status_code,403)

    def test_parent_return_allows_rewriting_and_preserves_original_submission(self):
        self.complete_and_submit()
        old=self.task.current
        decide_task(self.parent,self.task.pk,old.pk,'revision')
        response=self.save_answer(0,9)
        self.assertEqual(response.status_code,200)
        recognized=[{'text':'Rewritten answer','confidence':.95}]
        with patch('workshop.dictation.recognize_strokes',side_effect=recognized + [
            {'text':f'English answer {index}','confidence':.9} for index in range(2,10)]):
            self.assertRedirects(self.client.post(self.url,{
                'operation':'submit_handwriting_draft','revision':10}),self.url)
        self.task.refresh_from_db();old.refresh_from_db()
        self.assertEqual(self.task.current.number,2)
        self.assertIn('English answer 1',old.result)
        self.assertIn('Rewritten answer',self.task.current.result)

    def test_ai_pass_awards_points_and_renders_parent_result_without_letters(self):
        self.complete_and_submit()
        output={'grade':'B','reason':'译文准确完整','suggestions':'保持练习','knowledge':[],'mistakes':[]}
        with patch('workshop.worker.generate',return_value=(output,'real-model',{},{})):
            self.assertTrue(run_ai_once())
        self.assertEqual(Wallet.objects.get(child=self.child).points,10)
        self.client.force_login(self.parent)
        response=self.client.get(self.url)
        self.assertContains(response,'AI批改结果 · 通过')
        self.assertNotContains(response,'批改结果 · B')

    def test_completed_task_cannot_save_more_handwriting(self):
        self.complete_and_submit()
        decide_task(self.parent,self.task.pk,self.task.current_id,'complete')
        response=self.save_answer(0,9)
        self.assertEqual(response.status_code,400)
        self.assertIn('已完结',response.json()['error'])

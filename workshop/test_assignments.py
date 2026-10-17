from django.test import TestCase
from django.urls import reverse

from .forms import ParentTaskForm
from .models import ParentChild, Subject, Task, TaskTemplate, User, ReviewJob
from .services import create_task, save_draft, submit_task


class ParentAssignmentTests(TestCase):
    def setUp(self):
        self.parent = User.objects.create_user('assign-parent', is_parent=True)
        self.child = User.objects.create_user('assign-child', is_student=True)
        ParentChild.objects.create(parent=self.parent, child=self.child)
        self.parent.refresh_from_db()
        self.english = Subject.objects.create(name='英语')
        self.math = Subject.objects.create(name='数学')
        self.chinese = Subject.objects.create(name='语文')
        Subject.objects.create(name='科学')
        self.client.force_login(self.parent)
        self.url = reverse('task_create') + f'?child={self.child.pk}'

    def payload(self, **overrides):
        data = {'subject':str(self.english.pk), 'english_category':'vocabulary',
                'words':'1. apple##苹果##\n2. book##书##', 'phrases':'1. look after##照顾##',
                'sentences':'1. Practice makes perfect.##熟能生巧。##', 'kind':'daily'}
        data.update(overrides)
        return data

    def test_legacy_template_is_not_implicitly_used_or_configurable(self):
        template=TaskTemplate.objects.create(child=self.child,subject=self.english,title='旧英语模板',requirements='旧模板要求')
        task=create_task(self.parent,self.child,'手动任务',self.english)
        self.assertIsNone(task.template_id)
        self.assertEqual(task.template_snapshot['requirements'],'')
        url=reverse('manage_section',args=['templates'])+f'?child={self.child.pk}'
        self.assertEqual(self.client.get(url).status_code,410)
        self.assertEqual(self.client.post(url,{'title':'尝试修改旧模板','requirements':'旧内容'}).status_code,410)
        template.refresh_from_db()
        self.assertEqual(template.requirements,'旧模板要求')
        response=self.client.get(reverse('management')+f'?child={self.child.pk}')
        self.assertNotContains(response,'任务模板')

    def test_course_options_are_ordered_and_limited(self):
        form = ParentTaskForm()
        self.assertEqual(list(form.fields['subject'].queryset.values_list('name', flat=True)),
                         ['语文', '数学', '英语'])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="vocabulary-fields"')
        self.assertContains(response, 'assignment.js')
        self.assertContains(response, '课本翻译')

    def test_vocab_assignment_is_saved_and_frozen_in_submission(self):
        template = TaskTemplate.objects.create(child=self.child, subject=self.english,
            title='英语模板', requirements='先朗读再书写')
        response = self.client.post(self.url, self.payload(requirements='拍照上传成果'))
        self.assertEqual(response.status_code, 302)
        task = Task.objects.get(child=self.child)
        self.assertEqual(task.title, '英语 · 字词')
        self.assertEqual(task.template_snapshot['assignment']['content'], {
            'words':'1. apple##苹果##\n2. book##书##', 'phrases':'1. look after##照顾##', 'sentences':'1. Practice makes perfect.##熟能生巧。##'})
        self.assertNotIn('先朗读再书写', task.template_snapshot['requirements'])
        self.assertIsNone(task.template_id)
        self.assertEqual(task.template_snapshot['review_mode'], 'ai')
        self.assertIn('单词：\n1. apple##苹果##\n2. book##书##', task.template_snapshot['requirements'])
        self.assertEqual(task.requirements, '拍照上传成果')
        save_draft(self.child, task.pk, '我的具体要求', '已完成')
        sub = submit_task(self.child, task.pk)
        self.assertEqual(sub.snapshot['template']['assignment'], task.template_snapshot['assignment'])
        template.refresh_from_db()
        self.assertEqual(template.requirements, '先朗读再书写')

    def test_vocab_requires_at_least_one_field_and_retains_form_values(self):
        response = self.client.post(self.url, self.payload(words='', phrases='', sentences=''))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '单词、词组、句子至少填写一项')
        self.assertEqual(response.context['selected_course'], '英语')
        self.assertEqual(response.context['form']['english_category'].value(), 'vocabulary')
        self.assertFalse(Task.objects.exists())

    def test_individual_vocab_fields_are_optional(self):
        form = ParentTaskForm(self.payload(phrases='', sentences=''))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.task_data()['assignment']['content']['words'], '1. apple##苹果##\n2. book##书##')

    def test_each_vocabulary_field_rejects_missing_order_and_does_not_save(self):
        for field, value, name in [('words', '1. book##书##\napple##苹果##', 'apple'),
                                   ('phrases', 'look after##照顾##', 'look after'),
                                   ('sentences', 'Practice makes perfect.##熟能生巧。##', 'Practice makes perfect.'),
                                   ('words', '苹果', '苹果')]:
            with self.subTest(field=field, value=value):
                response = self.client.post(self.url, self.payload(**{field:value}))
                self.assertEqual(response.status_code, 200)
                message = f'{name}前面没有带上有序符号'
                self.assertContains(response, message, count=1)
                self.assertIn(field, response.context['form'].errors)
                self.assertEqual(response.context['form'].errors[field], [message])
                self.assertRegex(response.content.decode(),
                    rf'(?s)<textarea[^>]*name="{field}"[^>]*>.*?</textarea>\s*<ul class="errorlist"[^>]*><li>{message}</li></ul>')
                self.assertNotContains(response, '请检查以下内容')
                self.assertEqual(response.context['form'][field].value(), value)
                self.assertFalse(Task.objects.exists())

    def test_number_styles_are_accepted_and_do_not_pollute_reference_answers(self):
        from .dictation import questions
        for prefix in ['1.', '2、', '3)', '（4）', '(5)', '6．', '7）']:
            with self.subTest(prefix=prefix):
                form = ParentTaskForm(self.payload(words=f'  {prefix} apple##苹果##', phrases='', sentences=''))
                self.assertTrue(form.is_valid(), form.errors)
                task = create_task(self.parent, self.child, **form.task_data())
                self.assertEqual(questions(task), [{'prompt':'苹果', 'expected':'apple', 'label':'单词'}])

    def test_bullets_and_bare_numbers_do_not_count_as_order_symbols(self):
        for value in ['- apple##苹果##', 'apple2##苹果##', '1 apple##苹果##', '0. apple##苹果##']:
            with self.subTest(value=value):
                form = ParentTaskForm(self.payload(words=value))
                self.assertFalse(form.is_valid())
                self.assertEqual(form.errors['words'], [f'{value.split("##")[0]}前面没有带上有序符号'])

    def test_colon_or_incomplete_hash_format_is_rejected_for_new_tasks(self):
        for value in ['1. apple:苹果', '1. apple：苹果', '1. apple##苹果',
                      '1. apple##苹果##多余内容', '1. ##苹果##', '1. apple####']:
            with self.subTest(value=value):
                form = ParentTaskForm(self.payload(words=value, phrases='', sentences=''))
                self.assertFalse(form.is_valid())
                self.assertEqual(form.errors['words'], [
                    '每行请填写“序号. 英文##中文##”，例如“1. apple##苹果##”。'])

    def test_translation_ignores_stale_vocabulary_values(self):
        response = self.client.post(self.url, self.payload(
            english_category='translation', textbook_text='A little seed grows.##一颗小种子正在生长。##', title='第二课翻译'))
        self.assertEqual(response.status_code, 302)
        task = Task.objects.get()
        self.assertEqual(task.title, '第二课翻译')
        self.assertEqual(task.template_snapshot['assignment']['content'],
                         {'textbook_text':'A little seed grows.##一颗小种子正在生长。##'})
        self.assertNotIn('look after', task.template_snapshot['requirements'])

    def test_translation_avoids_demo_template_and_still_queues_ai(self):
        TaskTemplate.objects.create(child=self.child,subject=self.english,title='演示模板',
            requirements='朗读课文 10 分钟，记录最喜欢的表达。')
        response=self.client.post(self.url,self.payload(english_category='translation',textbook_text='A little seed grows.##一颗小种子正在生长。##'))
        self.assertEqual(response.status_code,302)
        task=Task.objects.get()
        self.assertIsNone(task.template_id)
        self.assertNotIn('朗读课文',task.template_snapshot['requirements'])
        self.assertIn('A little seed grows.',task.template_snapshot['requirements'])
        self.client.force_login(self.child)
        response=self.client.get(reverse('task_detail',args=[task.pk]))
        self.assertNotContains(response,'朗读课文')
        self.assertNotContains(response,'模板要求')
        self.assertContains(response,'查看中文题目')
        self.assertContains(response,'一颗小种子正在生长。')
        self.assertNotContains(response,'A little seed grows.')
        save_draft(self.child,task.pk,result='一颗小种子正在生长。')
        sub=submit_task(self.child,task.pk)
        self.assertEqual(ReviewJob.objects.get(submission=sub).status,'pending')

    def test_missing_category_or_translation_source_is_rejected(self):
        for overrides in ({'english_category':''}, {'english_category':'invalid'},
                          {'english_category':'translation', 'textbook_text':''}):
            with self.subTest(overrides=overrides):
                form = ParentTaskForm(self.payload(**overrides))
                self.assertFalse(form.is_valid())

    def test_translation_requires_hash_pairs_and_extracts_chinese(self):
        from .dictation import questions
        source=('Millie: I drink milk every day. How about you, Daniel? '
                '##米莉：我每天都喝牛奶。你呢，丹尼尔？##')
        form=ParentTaskForm(self.payload(english_category='translation',textbook_text=source))
        self.assertTrue(form.is_valid(),form.errors)
        task=create_task(self.parent,self.child,**form.task_data())
        self.assertEqual(questions(task),[{
            'expected':'Millie: I drink milk every day. How about you, Daniel?',
            'prompt':'米莉：我每天都喝牛奶。你呢，丹尼尔？','label':'课本翻译',
        }])
        for invalid in ['A little seed grows.', 'English##中文', 'English##中文##多余']:
            with self.subTest(invalid=invalid):
                form=ParentTaskForm(self.payload(english_category='translation',textbook_text=invalid))
                self.assertFalse(form.is_valid())
                self.assertIn('英文##中文##',str(form.errors['textbook_text']))

    def test_unfinished_courses_cannot_submit_english_content(self):
        for subject in [self.math, self.chinese]:
            with self.subTest(subject=subject.name):
                response = self.client.post(self.url, self.payload(subject=str(subject.pk)))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, '任务布置表单稍后开放')
        self.assertFalse(Task.objects.exists())

    def test_assignment_cannot_target_unrelated_child(self):
        other = User.objects.create_user('unrelated-child', is_student=True)
        response = self.client.post(reverse('task_create') + f'?child={other.pk}', self.payload())
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Task.objects.exists())

    def add_second_child(self):
        child = User.objects.create_user('second-assignment-child', first_name='小苗', is_student=True)
        ParentChild.objects.create(parent=self.parent, child=child)
        self.parent.refresh_from_db()
        self.client.force_login(self.parent)
        return child

    def test_student_selection_contains_only_linked_students(self):
        child = self.add_second_child()
        other = User.objects.create_user('not-in-student-selector', is_student=True)
        response = self.client.get(reverse('task_create'), {'child':child.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['child'], child)
        self.assertEqual(set(response.context['children']), {self.child, child})
        self.assertContains(response, 'id="assignment-student"')
        self.assertContains(response, '小苗（second-assignment-child）')
        self.assertNotContains(response, other.username)
        html = response.content.decode()
        self.assertLess(html.index('<h2>选择学生</h2>'), html.index('<h2>选择课程与内容</h2>'))

    def test_selected_student_assignment_does_not_inherit_default_templates(self):
        child = self.add_second_child()
        TaskTemplate.objects.create(child=self.child, subject=self.english, title='孩子一模板',
            requirements='孩子一要求')
        own_template = TaskTemplate.objects.create(child=child, subject=self.english, title='孩子二模板',
            requirements='孩子二要求')
        # Posted selection wins over an old URL parameter, but must still be explicitly associated.
        response = self.client.post(self.url, self.payload(child=child.pk))
        self.assertEqual(response.status_code, 302)
        task = Task.objects.get()
        self.assertEqual(task.child, child)
        self.assertIsNone(task.template)
        self.assertNotIn('rewards', task.template_snapshot)
        self.assertNotIn('孩子二要求', task.template_snapshot['requirements'])
        self.assertNotIn('孩子一要求', task.template_snapshot['requirements'])
        self.assertFalse(Task.objects.filter(child=self.child).exists())

    def test_posted_student_cannot_bypass_family_binding(self):
        other = User.objects.create_user('forged-selected-student', is_student=True)
        response = self.client.post(self.url, self.payload(child=other.pk))
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Task.objects.exists())

    def test_each_student_sees_only_their_own_assigned_tasks(self):
        child = self.add_second_child()
        self.client.post(self.url, self.payload(child=self.child.pk, title='第一个孩子的任务'))
        self.client.post(self.url, self.payload(child=child.pk, title='第二个孩子的任务', words='1. garden##花园##'))
        self.assertEqual(Task.objects.filter(child=self.child).count(), 1)
        self.assertEqual(Task.objects.filter(child=child).count(), 1)
        second_task = Task.objects.get(child=child)
        self.client.force_login(self.child)
        response = self.client.get(reverse('tasks'))
        self.assertContains(response, '第一个孩子的任务')
        self.assertNotContains(response, '第二个孩子的任务')
        self.assertEqual(self.client.get(reverse('task_detail', args=[second_task.pk])).status_code, 403)

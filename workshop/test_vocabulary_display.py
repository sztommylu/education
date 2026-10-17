from copy import deepcopy

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .models import ParentChild, Subject, User
from .presentation import vocabulary_sections
from .services import create_task, save_draft, submit_task


class VocabularyParsingTests(SimpleTestCase):
    def test_hash_format_extracts_only_chinese_and_preserves_colons(self):
        sections = vocabulary_sections(self.snapshot(
            words='1. apple##苹果##', phrases='1. look after##照顾##',
            sentences="1. It's 3:00.##现在是三点：请准时。##"))
        self.assertEqual(sections, [
            {'label':'单词', 'items':['苹果']},
            {'label':'词组', 'items':['照顾']},
            {'label':'句子', 'items':['现在是三点：请准时。']},
        ])

    def test_numbered_content_displays_only_chinese(self):
        sections = vocabulary_sections(self.snapshot(words='1. apple:苹果',
            phrases='1、look after:照顾', sentences='（1）This is a tree.:这是一棵树。'))
        self.assertEqual([section['items'] for section in sections], [['苹果'], ['照顾'], ['这是一棵树。']])

    def snapshot(self, **content):
        return {'assignment':{'category':'vocabulary', 'content':content}}

    def test_both_colon_types_blank_lines_and_first_colon_only(self):
        sections = vocabulary_sections(self.snapshot(
            words='apple: 苹果\r\n\r\nbook：书\nmeaning:释义：补充说明',
            phrases='look after： 照顾 ', sentences='This is a tree.:这是一棵树。'))
        self.assertEqual(sections, [
            {'label':'单词', 'items':['苹果','书','释义：补充说明']},
            {'label':'词组', 'items':['照顾']},
            {'label':'句子', 'items':['这是一棵树。']},
        ])

    def test_missing_or_empty_translation_never_exposes_english_prefix(self):
        sections = vocabulary_sections(self.snapshot(words='english_without_colon\nword:\nword：  '))
        self.assertEqual(len(sections), 3)
        self.assertTrue(all(not section['items'] for section in sections))

    def test_other_task_categories_are_not_transformed(self):
        self.assertEqual(vocabulary_sections({'assignment':{'category':'translation'}}), [])
        self.assertEqual(vocabulary_sections({}), [])

    def test_source_snapshot_is_not_mutated(self):
        snapshot = self.snapshot(words='apple:苹果')
        original = deepcopy(snapshot)
        vocabulary_sections(snapshot)
        self.assertEqual(snapshot, original)


class StudentVocabularyDisplayTests(TestCase):
    def setUp(self):
        self.parent = User.objects.create_user('vocab-parent', is_parent=True)
        self.child = User.objects.create_user('vocab-student', is_student=True)
        ParentChild.objects.create(parent=self.parent, child=self.child)
        self.subject = Subject.objects.create(name='英语')
        self.assignment = {'version':1,'course':'英语','category':'vocabulary',
            'category_label':'字词', 'content':{
                'words':'apple:苹果\nbook：书', 'phrases':'look after:照顾',
                'sentences':'This is a tree.：这是一棵树。'}}
        self.task = create_task(self.parent, self.child, '英语字词练习', self.subject,
            requirements='默写后拍照上传。', assignment=self.assignment)
        self.url = reverse('task_detail', args=[self.task.pk])
        self.client.force_login(self.child)

    def test_student_has_three_separate_sections_and_only_translations(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="vocabulary-section"', count=3)
        for label in ['单词', '词组', '句子']:
            self.assertContains(response, f'<h3>{label}</h3>')
        for text in ['苹果', '书', '照顾', '这是一棵树。', '默写后拍照上传。']:
            self.assertContains(response, text)
        for source in ['apple', 'look after', 'This is a tree.']:
            self.assertNotContains(response, source)

    def test_historical_requirements_also_hide_english_without_changing_versions(self):
        save_draft(self.child, self.task.pk, self.task.requirements, '已在纸上完成')
        sub = submit_task(self.child, self.task.pk)
        original = deepcopy(sub.snapshot)
        response = self.client.get(self.url)
        self.assertNotContains(response, '成长足迹')
        self.assertNotContains(response, 'apple')
        self.assertNotContains(response, 'look after')
        sub.refresh_from_db()
        self.assertEqual(sub.snapshot, original)
        self.assertIn('apple:苹果', sub.snapshot['template']['requirements'])

    def test_parent_still_sees_original_assignment_for_review(self):
        self.parent.refresh_from_db()
        self.client.force_login(self.parent)
        response = self.client.get(self.url)
        self.assertContains(response, 'apple:苹果')
        self.assertContains(response, 'look after:照顾')

    def test_translation_text_is_safely_escaped(self):
        self.assignment['content']['words'] = 'source:中文<script>alert(1)</script>'
        task = create_task(self.parent, self.child, '安全展示', self.subject, assignment=self.assignment)
        response = self.client.get(reverse('task_detail', args=[task.pk]))
        self.assertContains(response, '中文&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')

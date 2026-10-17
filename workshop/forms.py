import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django import forms
from django.db.models import Case, When, IntegerField
from django.contrib.auth.forms import AuthenticationForm
from .models import *
from .vocabulary import ORDER_PREFIX, hash_vocabulary_parts, vocabulary_parts

class TaskForm(forms.Form):
    title = forms.CharField(label='任务标题', max_length=120)
    subject = forms.ModelChoiceField(label='科目', queryset=Subject.objects.all())
    kind = forms.ChoiceField(label='任务类型', choices=Task._meta.get_field('kind').choices)
    due_date = forms.DateField(label='长期任务截止日期（可选）', required=False, widget=forms.DateInput(attrs={'type':'date'}))
    requirements = forms.CharField(label='本次具体要求', max_length=20000, required=False, widget=forms.Textarea(attrs={'rows':4}))

class ParentTaskForm(TaskForm):
    english_category = forms.ChoiceField(label='英语任务内容', required=False, choices=[('', '请选择内容类型'), ('vocabulary', '字词'), ('translation', '课本翻译')])
    words = forms.CharField(label='单词', max_length=5000, required=False, widget=forms.Textarea(attrs={'rows':5, 'placeholder':'每行填写一项，格式：序号. 英文##中文##\n1. apple##苹果##\n2. beautiful##美丽的##\n3. remember##记住##'}))
    phrases = forms.CharField(label='词组', max_length=5000, required=False, widget=forms.Textarea(attrs={'rows':5, 'placeholder':'每行填写一项，格式：序号. 英文##中文##\n1. look after##照顾##\n2. a little bit##一点点##\n3. in front of##在前面##'}))
    sentences = forms.CharField(label='句子', max_length=5000, required=False, widget=forms.Textarea(attrs={'rows':5, 'placeholder':'每行填写一项，格式：序号. 英文##中文##\n1. How was your weekend?##你周末过得怎么样？##\n2. Practice makes perfect.##熟能生巧。##'}))
    textbook_text = forms.CharField(label='课本翻译内容', max_length=10000, required=False, widget=forms.Textarea(attrs={'rows':8, 'placeholder':'每行一题：英文##中文##\nMillie drinks milk every day.##米莉每天都喝牛奶。##'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['subject'].label = '课程'
        self.fields['subject'].empty_label = '请选择课程'
        self.fields['subject'].queryset = Subject.objects.filter(name__in=['语文','数学','英语']).order_by(Case(When(name='语文', then=0), When(name='数学', then=1), default=2, output_field=IntegerField()))
        self.fields['title'].required = False
        self.fields['title'].label = '标题（可选）'
        self.fields['title'].widget.attrs['placeholder'] = '留空则根据课程和内容类型自动命名'
        self.fields['requirements'].label = '要求（可选）'
        self.fields['requirements'].widget.attrs['placeholder'] = '例如：根据中文提示手写英文，注意大小写。'
        self.fields['kind'].widget.attrs['aria-describedby'] = 'task-kind-help'

    def clean(self):
        data = super().clean()
        subject = data.get('subject')
        if not subject:
            return data
        if subject.name != '英语':
            self.add_error('subject', f'{subject.name}的任务布置表单稍后开放，请先选择英语。')
            return data
        category = data.get('english_category')
        if category not in ('vocabulary', 'translation'):
            if 'english_category' not in self.errors:
                self.add_error('english_category', '请选择字词或课本翻译。')
            return data
        if category == 'vocabulary':
            content = {key:data.get(key,'').strip() for key in ('words','phrases','sentences')}
            if not any(content.values()):
                self.add_error(None, '单词、词组、句子至少填写一项。')
            count = 0
            for key, value in content.items():
                lines = [line for line in value.splitlines() if line.strip()]
                count += len(lines)
                task = getattr(self, 'task', None)
                old_value = task.template_snapshot.get('assignment', {}).get('content', {}).get(key, '').strip() if task else None
                unchanged_legacy = task is not None and old_value == value and any(not hash_vocabulary_parts(line) for line in lines)
                for line in lines:
                    parts = vocabulary_parts(line)
                    if unchanged_legacy:
                        continue
                    if not ORDER_PREFIX.match(line):
                        name = parts[0] or line.strip()
                        self.add_error(key, f'{name}前面没有带上有序符号')
                    elif not hash_vocabulary_parts(line):
                        self.add_error(key, '每行请填写“序号. 英文##中文##”，例如“1. apple##苹果##”。')
            if count > 100:
                self.add_error(None, '一次最多布置 100 项，请拆分任务。')
            label = '字词'
        else:
            content = {'textbook_text':data.get('textbook_text','').strip()}
            if not content['textbook_text']:
                self.add_error('textbook_text', '请填写课本翻译内容。')
            else:
                lines = [line for line in content['textbook_text'].splitlines() if line.strip()]
                task = getattr(self, 'task', None)
                old_value = task.template_snapshot.get('assignment', {}).get('content', {}).get('textbook_text', '').strip() if task else None
                unchanged_legacy = task is not None and old_value == content['textbook_text'] and any(not hash_vocabulary_parts(line) for line in lines)
                if not unchanged_legacy:
                    for line in lines:
                        if not hash_vocabulary_parts(line):
                            self.add_error('textbook_text', '每行请填写“英文##中文##”，例如“Millie drinks milk.##米莉喝牛奶。##”。')
                if len(lines) > 100:
                    self.add_error('textbook_text', '一次最多布置 100 题，请拆分任务。')
            label = '课本翻译'
        data['title'] = data.get('title') or f'英语 · {label}'
        data['assignment'] = {'version':1, 'course':'英语', 'category':category, 'category_label':label, 'content':content}
        return data

    def task_data(self):
        return {key:self.cleaned_data[key] for key in ('title','subject','kind','due_date','requirements','assignment')}

class ParentTaskEditForm(ParentTaskForm):
    version = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, task, **kwargs):
        self.task = task
        super().__init__(*args, **kwargs)
        self.fields['subject'].disabled = True
        if task.current_id:
            self.fields['kind'].disabled = True
            self.fields['due_date'].disabled = True

class DraftForm(forms.Form):
    requirements = forms.CharField(label='本次具体要求', max_length=20000, required=False, widget=forms.Textarea(attrs={'rows':4}))
    result = forms.CharField(label='完成结果与学习心得', max_length=100000, required=False, widget=forms.Textarea(attrs={'rows':6}))
    attachment = forms.FileField(label='上传附件（每次一份，最多 50 MB）', required=False)
    purpose = forms.ChoiceField(label='附件用途', choices=Asset._meta.get_field('purpose').choices)
    remove = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple, label='从当前草稿移除附件')
    def __init__(self, *args, task=None, **kwargs):
        super().__init__(*args, **kwargs)
        if task: self.fields['remove'].choices = [(str(a.pk), a.name) for a in task.assets.filter(draft_active=True)]

class StudentSubmissionForm(forms.Form):
    attachment = forms.FileField(label='上传附件（每次一份，最多 50 MB）', required=False)

class HandwritingSubmissionForm(forms.Form):
    handwriting = forms.CharField(max_length=2_000_000, widget=forms.HiddenInput,
                                  error_messages={'required': '请先手写英文答案，再保存提交。'})

class ReviewForm(forms.Form):
    version = forms.IntegerField(widget=forms.HiddenInput)
    grade = forms.ChoiceField(label='初审等级', choices=[(x,x) for x in 'ABCD'])
    reason = forms.CharField(label='审核理由', widget=forms.Textarea(attrs={'rows':3}))
    suggestions = forms.CharField(label='修改建议', required=False, widget=forms.Textarea(attrs={'rows':3}))

class PointsWithdrawalForm(forms.Form):
    amount = forms.IntegerField(label='提现积分', min_value=1, max_value=100000000,
                                help_text='1 积分＝1 元，按整数积分申请提现。')
    request_key = forms.UUIDField(widget=forms.HiddenInput)

class ExtraBenefitForm(forms.Form):
    reason = forms.CharField(label='福利事由', max_length=200,
                             widget=forms.TextInput(attrs={'placeholder':'例如：主动阅读、帮助做家务'}))
    points = forms.IntegerField(label='奖励积分', min_value=1, max_value=1000000,
                               help_text='直接计入学生积分余额，1 积分＝1 元。')
    request_key = forms.UUIDField(widget=forms.HiddenInput)

class RuleForm(forms.ModelForm):
    class Meta:
        model=SubjectRule
        fields=['subject','prompt']
        labels={'subject':'科目','prompt':'学科评分与反馈规则'}

class SiteConfigForm(forms.ModelForm):
    class Meta:
        model = SiteConfig
        exclude = ['id','logo']
        help_texts = {
            'ai_url': 'DeepSeek 请填写完整地址：https://api.deepseek.com/chat/completions；Gemini 填写 API 基础地址。',
            'ai_model': '填写供应商支持的模型名称；DeepSeek 可参考其控制台的可用模型列表。密钥由服务器环境变量 AI_API_KEY 提供。',
        }
    logo_upload = forms.FileField(label='上传 Logo', required=False)
    def clean_logo_upload(self):
        image=self.cleaned_data.get('logo_upload')
        if image:
            from .resources import sniff
            if not sniff(image).startswith('image/'): raise forms.ValidationError('Logo 必须为图片')
        return image
    def clean_timezone(self):
        value=self.cleaned_data['timezone']
        try: ZoneInfo(value)
        except ZoneInfoNotFoundError: raise forms.ValidationError('无效的 IANA 时区')
        return value
    def clean_encouragements(self):
        val=self.cleaned_data['encouragements']
        if not isinstance(val,list) or any(not isinstance(x,str) for x in val): raise forms.ValidationError('鼓励语应为字符串数组')
        return val
    def clean_theme_color(self):
        import re
        value=self.cleaned_data['theme_color']
        if not re.fullmatch(r'#[0-9a-fA-F]{6}',value): raise forms.ValidationError('请输入 #RRGGBB')
        vals=[int(value[i:i+2],16)/255 for i in (1,3,5)]
        linear=[v/12.92 if v<=0.04045 else ((v+0.055)/1.055)**2.4 for v in vals]
        luminance=sum(a*b for a,b in zip(linear,[0.2126,0.7152,0.0722]))
        if 1.05/(luminance+0.05)<4.5: raise forms.ValidationError('主题色与白色文字的对比度应至少为 4.5:1，请选择更深颜色')
        return value

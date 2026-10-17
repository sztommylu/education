import uuid
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

class User(AbstractUser):
    is_student = models.BooleanField("学生", default=False)
    is_parent = models.BooleanField("家长", default=False)
    session_epoch = models.PositiveIntegerField(default=0, editable=False)
    reduce_motion = models.BooleanField("关闭动效", default=False)
    def __str__(self): return self.first_name or self.username

class ParentChild(models.Model):
    parent = models.ForeignKey(User, on_delete=models.CASCADE, related_name="children_links")
    child = models.ForeignKey(User, on_delete=models.CASCADE, related_name="parent_links")
    class Meta:
        constraints = [models.UniqueConstraint(fields=["parent", "child"], name="unique_parent_child")]

class Identity(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    issuer = models.URLField()
    subject = models.CharField(max_length=255)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["issuer", "subject"], name="unique_external_identity")]

class SiteConfig(models.Model):
    name = models.CharField(max_length=80, default="成长工坊")
    subtitle = models.CharField(max_length=200, default="每一小步，都在向上生长")
    timezone = models.CharField(max_length=80, default="Asia/Shanghai")
    theme_color = models.CharField(max_length=7, default="#315e45")
    animation_theme = models.CharField("动效主题（留空使用所有主题）", max_length=40, blank=True)
    encouragements = models.JSONField(default=list, blank=True)
    global_prompt = models.TextField(default="A 优秀、B 良好、C 达标、D 未达标。遵循学科规则，按独立小题去重，区分计算、理解、格式和材料辨认问题，不以一处错误统一判 D。无法识别或主要要求未完成需解释。")
    prompt_version = models.CharField(max_length=30, default="1")
    ai_adapter = models.CharField(max_length=30, choices=[("mock", "开发模拟"), ("deepseek", "DeepSeek"), ("gemini", "Gemini 多模态")], default="mock")
    ai_url = models.URLField(default="https://api.openai.com/v1/chat/completions")
    ai_model = models.CharField(max_length=100, default="configure-model")
    logo = models.ForeignKey("Asset", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    @classmethod
    def load(cls): return cls.objects.get_or_create(pk=1)[0]
    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

class Subject(models.Model):
    name = models.CharField("科目", max_length=40, unique=True)
    icon = models.CharField(max_length=10, default="📘")
    def __str__(self): return self.name

class SubjectRule(models.Model):
    child = models.ForeignKey(User, on_delete=models.CASCADE)
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT)
    prompt = models.TextField("学科评分与反馈规则")
    class Meta:
        constraints = [models.UniqueConstraint(fields=["child", "subject"], name="unique_subject_rule")]

class TaskTemplate(models.Model):
    child = models.ForeignKey(User, on_delete=models.PROTECT)
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT)
    title = models.CharField(max_length=120)
    requirements = models.TextField()
    review_mode = models.CharField(max_length=10, choices=[("ai", "AI 审核"), ("parent", "家长审核")], default="ai")
    enabled = models.BooleanField(default=True)
    auto_generate = models.BooleanField(default=False)
    weekdays = models.JSONField(default=list, help_text="0 为周一，6 为周日")
    external_plan = models.CharField(max_length=200, blank=True)
    def __str__(self): return self.title

class Task(models.Model):
    PHASES = [("draft", "待提交"), ("waiting", "等待审核"), ("revision", "待修改"), ("passed", "等待审核"), ("awarded", "完成"), ("rejected", "被驳回")]
    child = models.ForeignKey(User, on_delete=models.PROTECT)
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT)
    template = models.ForeignKey(TaskTemplate, null=True, blank=True, on_delete=models.PROTECT)
    title = models.CharField(max_length=120)
    kind = models.CharField(max_length=10, choices=[("daily", "日常任务"), ("long", "长期任务")], default="daily")
    day = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    auto_key = models.CharField(max_length=160, null=True, blank=True, unique=True)
    template_snapshot = models.JSONField(default=dict)
    requirements = models.TextField(blank=True)
    result = models.TextField(blank=True)
    handwriting = models.JSONField(default=dict, blank=True)
    handwriting_draft = models.JSONField(default=dict, blank=True)
    phase = models.CharField(max_length=12, choices=PHASES, default="draft")
    current = models.ForeignKey("Submission", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    @property
    def closed(self): return self.phase in ("awarded", "rejected")
    def __str__(self): return self.title

class Asset(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.PROTECT, related_name="assets")
    category = models.CharField(max_length=20, choices=[("attachment", "任务附件"), ("brand", "品牌"), ("animation", "动效")], default="attachment")
    purpose = models.CharField(max_length=20, choices=[("requirements", "任务要求"), ("result", "完成结果")], default="result")
    file = models.FileField(upload_to="private/%Y/%m/%d")
    name = models.CharField(max_length=200)
    mime = models.CharField(max_length=120)
    size = models.PositiveBigIntegerField()
    draft_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

class Submission(models.Model):
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="versions")
    number = models.PositiveIntegerField()
    requirements = models.TextField()
    result = models.TextField()
    snapshot = models.JSONField(default=dict)
    attachments = models.ManyToManyField(Asset, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["task", "number"], name="unique_task_version")]
    def save(self, *args, **kwargs):
        if self.pk: raise ValueError("正式提交不可修改")
        super().save(*args, **kwargs)

class Review(models.Model):
    submission = models.OneToOneField(Submission, on_delete=models.PROTECT, related_name="review")
    source = models.CharField(max_length=10)
    grade = models.CharField(max_length=1, choices=[(x,x) for x in "ABCD"])
    reason = models.TextField()
    suggestions = models.TextField(blank=True)
    output = models.JSONField(default=dict)
    actual_model = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class ReviewJob(models.Model):
    submission = models.OneToOneField(Submission, on_delete=models.PROTECT)
    frozen = models.JSONField(default=dict)
    public_error = models.CharField(max_length=1200, blank=True)
    status = models.CharField(max_length=20, default="pending")
    attempts = models.PositiveIntegerField(default=0)
    next_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class AIRun(models.Model):
    job = models.ForeignKey(ReviewJob, on_delete=models.PROTECT, related_name="runs")
    attempt = models.PositiveIntegerField()
    actual_model = models.CharField(max_length=100, blank=True)
    request = models.JSONField(default=dict)
    response = models.TextField(blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class AnalysisRecord(models.Model):
    child = models.ForeignKey(User, on_delete=models.PROTECT)
    submission = models.ForeignKey(Submission, on_delete=models.PROTECT)
    review = models.ForeignKey(Review, on_delete=models.PROTECT)
    kind = models.CharField(max_length=20, choices=[("knowledge", "知识点观察"), ("mistake", "错题")])
    data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

class Settlement(models.Model):
    task = models.OneToOneField(Task, on_delete=models.PROTECT, related_name="settlement")
    submission = models.ForeignKey(Submission, on_delete=models.PROTECT)
    parent = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT)
    source = models.CharField(max_length=10, choices=[('parent', '家长确认'), ('ai', 'AI 通过')], default='parent')
    grade = models.CharField(max_length=1)
    points = models.PositiveIntegerField(default=0)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class Wallet(models.Model):
    child = models.OneToOneField(User, primary_key=True, on_delete=models.PROTECT)
    points = models.PositiveBigIntegerField(default=0)
    frozen_points = models.PositiveBigIntegerField(default=0)
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(points__gte=models.F('frozen_points')), name='frozen_points_within_balance')]
    @property
    def points_value_cents(self): return self.points * 100
    @property
    def available_points(self): return self.points - self.frozen_points
    @property
    def available_points_value_cents(self): return self.available_points * 100

class PointEntry(models.Model):
    child = models.ForeignKey(User, on_delete=models.PROTECT)
    task = models.OneToOneField(Task, null=True, blank=True, on_delete=models.PROTECT, related_name='point_entry')
    withdrawal = models.OneToOneField('Withdrawal', null=True, blank=True, on_delete=models.PROTECT, related_name='point_entry')
    extra_benefit = models.OneToOneField('ExtraBenefitGrant', null=True, blank=True, on_delete=models.PROTECT, related_name='point_entry')
    points = models.IntegerField()
    reason = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.CheckConstraint(
            condition=Q(task__isnull=False, withdrawal__isnull=True, extra_benefit__isnull=True, points__gt=0)
                      | Q(task__isnull=True, withdrawal__isnull=False, extra_benefit__isnull=True, points__lt=0)
                      | Q(task__isnull=True, withdrawal__isnull=True, extra_benefit__isnull=False, points__gt=0),
            name='point_entry_credit_or_withdrawal',
        )]

class Withdrawal(models.Model):
    child = models.ForeignKey(User, on_delete=models.PROTECT)
    cents = models.PositiveBigIntegerField(validators=[MinValueValidator(1)])
    status = models.CharField(max_length=12, choices=[("pending", "待处理"), ("paid", "已支付"), ("rejected", "已拒绝"), ("cancelled", "已取消")], default="pending")
    request_key = models.UUIDField(default=uuid.uuid4, unique=True)
    handled_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="handled_withdrawals")
    created_at = models.DateTimeField(auto_now_add=True)
    @property
    def points_amount(self): return self.cents // 100

class ExtraBenefitGrant(models.Model):
    child = models.ForeignKey(User, on_delete=models.PROTECT)
    parent = models.ForeignKey(User, on_delete=models.PROTECT, related_name='extra_benefits_given')
    reason = models.CharField(max_length=200)
    points = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    request_key = models.UUIDField(unique=True, default=uuid.uuid4)
    created_at = models.DateTimeField(auto_now_add=True)

class Notification(models.Model):
    child = models.ForeignKey(User, on_delete=models.CASCADE)
    kind = models.CharField(max_length=20)
    title = models.CharField(max_length=200)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class Animation(models.Model):
    name = models.CharField(max_length=80)
    scene = models.CharField(max_length=20, choices=[("submit", "submit")])
    theme = models.CharField(max_length=40, blank=True)
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT)
    enabled = models.BooleanField(default=True)

class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, unique=True)
    count = models.PositiveIntegerField(default=0)
    window = models.DateTimeField(default=timezone.now)

class FinalFeedback(models.Model):
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name='final_feedback')
    submission = models.ForeignKey(Submission, on_delete=models.PROTECT)
    parent = models.ForeignKey(User, on_delete=models.PROTECT)
    action = models.CharField(max_length=20)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

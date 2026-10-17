"""Business writes live here. Lock order: child -> wallet -> task/withdrawal.
SQLite IMMEDIATE transactions are for local use; PostgreSQL provides production row locks.
"""
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from .models import *
from .vocabulary import normalized_vocabulary

PASS = ('A', 'B', 'C')

def today():
    return timezone.now().astimezone(ZoneInfo(SiteConfig.load().timezone)).date()

def can_access(actor, child):
    return (actor.is_student and actor.pk == child.pk) or (actor.is_parent and ParentChild.objects.filter(parent=actor, child=child, child__is_student=True).exists())

def require_access(actor, child, parent=False):
    if not actor.is_active or not can_access(actor, child) or (parent and not actor.is_parent):
        raise PermissionDenied('无权访问该孩子的数据')

def lock_wallet(child):
    User.objects.select_for_update().get(pk=child.pk)
    Wallet.objects.get_or_create(child=child)
    return Wallet.objects.select_for_update().get(child=child)

def notify(child, kind, title):
    Notification.objects.create(child=child, kind=kind, title=title)

def freeze_template(template):
    if not template: return {'requirements': '', 'review_mode': 'parent', 'external_plan': ''}
    return {'requirements': template.requirements, 'review_mode': template.review_mode, 'external_plan': template.external_plan, 'title': template.title}

def ensure_today(child):
    """Legacy hook: tasks are now created explicitly by parents only."""
    return None

@transaction.atomic
def create_task(actor, child, title, subject, kind='daily', due_date=None, requirements='', template=None, assignment=None):
    require_access(actor, child, parent=True)
    lock_wallet(child)
    if template and (template.child_id != child.pk or template.subject_id != subject.pk or not template.enabled):
        raise ValidationError('模板与孩子或科目不匹配')
    if kind not in ('daily', 'long'): raise ValidationError('任务类型无效')
    snapshot = freeze_template(template)
    if assignment is not None:
        if template is None:
            snapshot['review_mode'] = 'ai'
        require_access(actor, child, parent=True)
        snapshot['base_requirements'] = snapshot['requirements']
        snapshot['assignment'] = assignment
        labels = {'words':'单词', 'phrases':'词组', 'sentences':'句子', 'textbook_text':'课本原文'}
        content = '\n\n'.join(f'{labels[key]}：\n{value}' for key,value in assignment['content'].items() if value)
        snapshot['requirements'] = '\n\n'.join(filter(None, [snapshot['requirements'], '家长布置内容 · ' + assignment['category_label'], content]))
    return Task.objects.create(child=child, subject=subject, title=title, kind=kind, due_date=due_date if kind == 'long' else None, day=today(), requirements=requirements, template=template, template_snapshot=snapshot)

def task_edit_version(task):
    import hashlib
    import json
    payload = [task.title, task.requirements, task.kind, str(task.due_date), task.template_snapshot,
               task.current_id, task.handwriting_draft.get('revision', 0), task.closed, task.result]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@transaction.atomic
def edit_task(actor, task_id, version, title, subject, kind, due_date, requirements, assignment):
    task = Task.objects.get(pk=task_id, deleted=False)
    require_access(actor, task.child, parent=True)
    lock_wallet(task.child)
    task = Task.objects.select_for_update().get(pk=task_id, deleted=False)
    if task.closed:
        raise ValidationError('已完结任务不能编辑。')
    if task_edit_version(task) != version:
        raise ValidationError('任务或学生进度已更新，请刷新后重新编辑。')
    if subject.pk != task.subject_id:
        raise ValidationError('已有任务不能更换课程，请另行布置任务。')
    old_assignment = task.template_snapshot.get('assignment', {})
    content_keys = ('words', 'phrases', 'sentences') if assignment['category'] == 'vocabulary' else ('textbook_text',)
    normalize = normalized_vocabulary if assignment['category'] == 'vocabulary' else str.strip
    content_changed = old_assignment.get('category') != assignment['category'] or any(normalize(old_assignment.get('content', {}).get(key, '')) != normalize(assignment['content'].get(key, '')) for key in content_keys)
    source_changed = old_assignment.get('category') != assignment['category'] or any(old_assignment.get('content', {}).get(key, '').strip() != assignment['content'].get(key, '').strip() for key in content_keys)
    started = task.current_id or task.handwriting or task.handwriting_draft.get('saved_indices') or task.result.strip() or task.assets.filter(draft_active=True, purpose='result').exists()
    if content_changed and started:
        raise ValidationError('学生已开始作答，不能修改题目内容；仍可修改标题和要求。新增题目请另行布置任务。')
    normalized_due = due_date if kind == 'long' else None
    if task.current_id and (kind != task.kind or normalized_due != task.due_date):
        raise ValidationError('正式提交后不能修改任务类型和截止日期。')
    if kind not in ('daily', 'long'):
        raise ValidationError('任务类型无效。')
    if source_changed:
        snapshot = dict(task.template_snapshot)
        base = snapshot.get('base_requirements')
        if base is None:
            base = snapshot.get('requirements', '').split('家长布置内容 · ', 1)[0].strip()
        labels = {'words':'单词', 'phrases':'词组', 'sentences':'句子', 'textbook_text':'课本原文'}
        content = '\n\n'.join(f'{labels[key]}：\n{value}' for key, value in assignment['content'].items() if value)
        snapshot.update(assignment=assignment, base_requirements=base,
                        requirements='\n\n'.join(filter(None, [base, '家长布置内容 · ' + assignment['category_label'], content])))
        task.template_snapshot = snapshot
        if content_changed:
            # Increment the draft version so already-open student pages cannot save against old questions.
            task.handwriting_draft = {'rows':[], 'saved_indices':[],
                                      'revision':task.handwriting_draft.get('revision', 0) + 1}
            from .dictation import questions
            task.handwriting_draft['rows'] = [{'strokes':[]} for _ in questions(task)]
    task.title, task.requirements, task.kind, task.due_date = title, requirements, kind, normalized_due
    task.save(update_fields=['title','requirements','kind','due_date','template_snapshot','handwriting_draft'])
    return task


@transaction.atomic
def save_draft(actor, task_id, requirements=None, result=None, remove_ids=()):
    task = Task.objects.get(pk=task_id, deleted=False)
    require_access(actor, task.child)
    lock_wallet(task.child)
    task = Task.objects.select_for_update().get(pk=task_id)
    if task.closed: raise ValidationError('已完结任务不能编辑')
    if requirements is not None:
        task.requirements = requirements
    if result is not None:
        task.result = result
    task.save(update_fields=['requirements', 'result'])
    task.assets.filter(pk__in=remove_ids).update(draft_active=False)
    return task

def editable_handwriting_task(actor, task_id):
    from .dictation import questions
    task = Task.objects.select_related('child', 'subject').get(pk=task_id, deleted=False)
    require_access(actor, task.child)
    if not actor.is_student or actor.is_parent:
        raise PermissionDenied('手写答案由学生提交')
    if task.closed:
        raise ValidationError('已完结任务不能编辑')
    if not questions(task):
        raise ValidationError('这不是英语字词默写任务')
    if task.phase != 'revision' and task.current_id and ReviewJob.objects.filter(submission_id=task.current_id, status__in=['pending', 'running']).exists():
        raise ValidationError('本次默写正在批改，请等待结果后再提交。')
    return task


@transaction.atomic
def save_handwriting_answer(actor, task_id, index, payload, revision):
    import json
    from .dictation import questions, parse_strokes, draft_state
    task = editable_handwriting_task(actor, task_id)
    lock_wallet(task.child)
    task = Task.objects.select_for_update().get(pk=task_id)
    editable_handwriting_task(actor, task_id)
    state = draft_state(task)
    if revision != state['revision']:
        raise ValidationError('答案已在其他页面更新，请刷新后继续。')
    if not 0 <= index < len(questions(task)):
        raise ValidationError('题号无效，请刷新页面。')
    row = parse_strokes(payload, 1)[0]
    state['rows'][index] = row
    # Validate total draft limits, not just the individual answer.
    parse_strokes(json.dumps(state['rows']), len(state['rows']))
    state['saved_indices'] = sorted(set(state['saved_indices']) | {index})
    state['revision'] += 1
    task.handwriting_draft = state
    task.save(update_fields=['handwriting_draft'])
    return {'revision':state['revision'], 'saved_indices':state['saved_indices']}


def submit_handwriting_draft(actor, task_id, revision):
    import json
    from .dictation import questions, draft_state
    task = editable_handwriting_task(actor, task_id)
    state = draft_state(task)
    count = len(questions(task))
    if revision != state['revision']:
        raise ValidationError('答案已在其他页面更新，请刷新后继续。')
    if set(state['saved_indices']) != set(range(count)):
        category = task.template_snapshot.get('assignment', {}).get('category')
        raise ValidationError('请先完成并保存所有单词、词组和句子。' if category == 'vocabulary'
                              else '请先完成并保存所有题目。')
    return save_handwriting_and_submit(actor, task_id, json.dumps(state['rows']), draft_revision=revision)


def save_handwriting_and_submit(actor, task_id, payload, draft_revision=None):
    from .dictation import questions, parse_strokes, recognize_answers
    task = Task.objects.select_related('child', 'subject').get(pk=task_id, deleted=False)
    require_access(actor, task.child)
    if not actor.is_student or actor.is_parent:
        raise PermissionDenied('手写答案由学生提交')
    if task.closed:
        raise ValidationError('已完结任务不能编辑')
    items = questions(task)
    if not items:
        raise ValidationError('这不是英语字词默写任务')
    rows = parse_strokes(payload, len(items))
    # Model inference happens outside database transactions.
    handwriting = recognize_answers(items, rows)
    with transaction.atomic():
        saved = save_draft(actor, task_id)
        if saved.phase != 'revision' and saved.current_id and ReviewJob.objects.filter(submission_id=saved.current_id, status__in=['pending', 'running']).exists():
            raise ValidationError('本次默写正在批改，请等待结果后再提交。')
        if draft_revision is not None:
            from .dictation import draft_state
            if draft_state(saved)['revision'] != draft_revision:
                raise ValidationError('识别期间答案已在其他页面更新，请刷新后重新提交。')
        saved.handwriting = handwriting
        translation = task.template_snapshot.get('assignment', {}).get('category') == 'translation'
        saved.result = '\n'.join(
            f"{i}. {row['recognized'] or '（未作答）'}" if translation
            else f"{i}. {row['prompt']}：{row['recognized'] or '（未作答）'}"
            for i, row in enumerate(handwriting['answers'], 1)
        )
        saved.save(update_fields=['handwriting', 'result'])
        return submit_task(actor, task_id)


@transaction.atomic
def submit_task(actor, task_id):
    task = Task.objects.get(pk=task_id, deleted=False)
    require_access(actor, task.child)
    lock_wallet(task.child)
    task = Task.objects.select_for_update().get(pk=task_id)
    if task.closed: raise ValidationError('已关闭任务不能再次提交')
    if not task.result.strip() and not task.assets.filter(draft_active=True, purpose='result').exists():
        raise ValidationError('请填写成果或上传成果附件')
    number = (task.versions.aggregate(n=Max('number'))['n'] or 0) + 1
    rule = SubjectRule.objects.filter(child=task.child, subject=task.subject).first()
    config = SiteConfig.load()
    snapshot = {'template': task.template_snapshot, 'subject': task.subject.name, 'subject_rule': rule.prompt if rule else '按任务主要要求与独立小题评估，提供具体反馈。', 'day': str(task.day), 'external_plan': task.template_snapshot.get('external_plan', '')}
    if task.handwriting:
        snapshot['dictation'] = task.handwriting
    sub = Submission.objects.create(task=task, number=number, requirements=task.requirements, result=task.result, snapshot=snapshot)
    if not task.handwriting and task.template_snapshot.get('assignment', {}).get('category') != 'translation':
        sub.attachments.set(task.assets.filter(draft_active=True))
    task.current, task.phase = sub, 'waiting'
    task.save(update_fields=['current', 'phase'])
    if task.handwriting or task.template_snapshot.get('assignment', {}).get('category') == 'translation' or task.template_snapshot.get('review_mode') == 'ai':
        from .ai import SCHEMA
        ReviewJob.objects.create(submission=sub, frozen={'global_prompt': config.global_prompt, 'subject_prompt': snapshot['subject_rule'], 'prompt_version': config.prompt_version, 'schema_version': '1', 'schema': SCHEMA, 'adapter': config.ai_adapter, 'model': config.ai_model, 'url': config.ai_url})
    return sub

@transaction.atomic
def record_review(submission_id, source, output, actual_model=''):
    sub = Submission.objects.select_related('task__child').get(pk=submission_id)
    w = lock_wallet(sub.task.child)
    task = Task.objects.select_for_update().get(pk=sub.task_id)
    if Review.objects.filter(submission=sub).exists(): return sub.review
    if output['grade'] not in ('A','B','C','D'): raise ValidationError('无效等级')
    review = Review.objects.create(submission=sub, source=source, grade=output['grade'], reason=output['reason'], suggestions=output.get('suggestions', ''), output=output, actual_model=actual_model)
    for data in output.get('knowledge', []):
        AnalysisRecord.objects.create(child=task.child, submission=sub, review=review, kind='knowledge', data=data)
    for data in output.get('mistakes', []):
        AnalysisRecord.objects.create(child=task.child, submission=sub, review=review, kind='mistake', data=data)
    _settle_ai_pass(task, w, review)
    return review

def _settle_ai_pass(task, wallet, review):
    if (review.source == 'ai' and review.grade in PASS and review.actual_model != 'mock-v1'
            and task.subject.name == '英语' and not task.deleted
            and task.current_id == review.submission_id and task.phase in ('waiting', 'passed')
            and not FinalFeedback.objects.filter(submission_id=review.submission_id, action='return').exists()):
        return _settle_locked_task(task, wallet, review.submission_id, 'settle', review.grade, points=10,
                                   source='ai', note='AI 批改通过，英语任务积分到账。')


@transaction.atomic
def settle_ai_passed_review(submission_id):
    review = Review.objects.select_related('submission__task__child').get(submission_id=submission_id)
    wallet = lock_wallet(review.submission.task.child)
    task = Task.objects.select_for_update().get(pk=review.submission.task_id)
    return _settle_ai_pass(task, wallet, review)


@transaction.atomic
def parent_review(actor, task_id, version_id, grade, reason, suggestions=''):
    task = Task.objects.get(pk=task_id, deleted=False)
    require_access(actor, task.child, parent=True)
    lock_wallet(task.child)
    task = Task.objects.select_for_update().get(pk=task_id)
    if task.closed or task.current_id != version_id: raise ValidationError('任务已关闭或版本已更新，请刷新后重试')
    job = ReviewJob.objects.filter(submission_id=version_id).first()
    if job and job.status in ('pending', 'running'): raise ValidationError('AI 正在处理，完成或转交后再进行初审')
    return record_review(version_id, 'parent', {'grade': grade, 'reason': reason, 'suggestions': suggestions, 'knowledge': [], 'mistakes': []})

@transaction.atomic
def finalize(actor, task_id, version_id, action, grade='C', note='', require_review=True, points=0):
    task = Task.objects.get(pk=task_id, deleted=False)
    require_access(actor, task.child, parent=True)
    w = lock_wallet(task.child)
    task = Task.objects.select_for_update().get(pk=task_id)
    if task.closed: raise ValidationError('该任务已经结算或驳回')
    if not task.current_id or task.current_id != version_id: raise ValidationError('提交版本已变化，请刷新后复核')
    if action == 'return':
        task.phase = 'revision'
        task.save(update_fields=['phase'])
        FinalFeedback.objects.create(task=task, submission_id=version_id, parent=actor, action=action, note=note)
        return
    if action not in ('reject', 'settle'): raise ValidationError('未知操作')
    if action == 'settle' and (grade not in PASS or (require_review and not hasattr(task.current, 'review'))): raise ValidationError('请先完成当前版本审核，再确认 A/B/C 等级')
    if type(points) is not int or points < 0 or points > 1000000: raise ValidationError('积分数值超出范围')
    if action == 'reject': grade, points = 'D', 0
    return _settle_locked_task(task, w, version_id, action, grade, points=points, parent=actor, note=note)


def _settle_locked_task(task, w, version_id, action, grade, points=0, parent=None,
                        source='parent', note=''):
    """Caller holds the child, wallet and task locks within one transaction."""
    settlement = Settlement.objects.create(task=task, submission_id=version_id, parent=parent, source=source, grade=grade, points=points, note=note)
    w.points += points
    w.save(update_fields=['points'])
    if points:
        PointEntry.objects.create(child=task.child, task=task, points=points, reason=task.title)
        notify(task.child, 'points', f'积分到账 +{points}：{task.title}')
    task.phase = 'rejected' if action == 'reject' else 'awarded'
    task.save(update_fields=['phase'])
    return settlement

@transaction.atomic
def decide_task(actor, task_id, version_id, decision):
    task = Task.objects.get(pk=task_id, deleted=False)
    require_access(actor, task.child, parent=True)
    lock_wallet(task.child)
    task = Task.objects.select_for_update().get(pk=task_id, deleted=False)
    if task.current_id != version_id or not version_id:
        raise ValidationError('提交版本已变化，请刷新后操作')
    if task.phase not in ('waiting', 'passed'):
        raise ValidationError('仅等待审核的任务可以处理，请刷新页面')
    if decision == 'revision':
        return finalize(actor, task_id, version_id, 'return', note='家长退回，请修改后重新提交。')
    if decision != 'complete':
        raise ValidationError('未知操作')
    review = getattr(task.current, 'review', None)
    grade = review.grade if review and review.grade in PASS else 'C'
    return finalize(actor, task_id, version_id, 'settle', grade=grade,
                    points=10 if task.subject.name == '英语' else 0,
                    note='家长确认完成。', require_review=False)

@transaction.atomic
def delete_task(actor, task_id):
    task = Task.objects.get(pk=task_id)
    require_access(actor, task.child, parent=True)
    lock_wallet(task.child)
    task = Task.objects.select_for_update().get(pk=task_id)
    if task.versions.exists(): raise ValidationError('正式提交后的任务不能删除')
    task.deleted = True
    task.save(update_fields=['deleted'])

@transaction.atomic
def grant_extra_benefit(actor, child, reason, points, request_key):
    require_access(actor, child, parent=True)
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 200:
        raise ValidationError('请填写不超过 200 字的福利事由')
    if type(points) is not int or not 1 <= points <= 1000000:
        raise ValidationError('奖励积分应为 1 至 1000000 的整数')
    reason = reason.strip()
    wallet = lock_wallet(child)
    existing = ExtraBenefitGrant.objects.filter(request_key=request_key).first()
    if existing:
        if (existing.child_id, existing.parent_id, existing.reason, existing.points) != (child.pk, actor.pk, reason, points):
            raise ValidationError('重复请求参数不一致，请刷新后重试')
        return existing
    grant = ExtraBenefitGrant.objects.create(child=child, parent=actor, reason=reason, points=points, request_key=request_key)
    wallet.points += points
    wallet.save(update_fields=['points'])
    PointEntry.objects.create(child=child, extra_benefit=grant, points=points, reason=reason)
    notify(child, 'points', f'额外福利 +{points} 积分：{reason}'[:200])
    return grant


@transaction.atomic
def request_withdrawal(actor, cents, request_key):
    if not actor.is_student: raise PermissionDenied
    w = lock_wallet(actor)
    existing = Withdrawal.objects.filter(request_key=request_key).first()
    if existing:
        if existing.child_id != actor.pk or existing.cents != cents: raise ValidationError('重复请求参数不一致')
        return existing
    if type(cents) is not int or cents <= 0: raise ValidationError('提现金额无效')
    if cents % 100 or cents // 100 > w.available_points: raise ValidationError('可用积分不足或提现积分无效')
    obj = Withdrawal.objects.create(child=actor, cents=cents, request_key=request_key)
    w.frozen_points += cents // 100
    w.save(update_fields=['frozen_points'])
    return obj

@transaction.atomic
def handle_withdrawal(actor, withdrawal_id, action):
    obj = Withdrawal.objects.select_related('child').get(pk=withdrawal_id)
    if action == 'cancel':
        if not actor.is_student or actor.pk != obj.child_id: raise PermissionDenied
    else: require_access(actor, obj.child, parent=True)
    if action not in ('cancel', 'pay', 'reject'): raise ValidationError('未知操作')
    w = lock_wallet(obj.child)
    obj = Withdrawal.objects.select_for_update().get(pk=obj.pk)
    if obj.status != 'pending': raise ValidationError('申请已处理')
    points = obj.cents // 100
    w.frozen_points -= points
    if action == 'pay':
        w.points -= points
        PointEntry.objects.create(child=obj.child, withdrawal=obj, points=-points, reason='积分提现，已线下支付')
    obj.status = {'cancel': 'cancelled', 'pay': 'paid', 'reject': 'rejected'}[action]
    obj.handled_by = actor
    obj.save()
    w.save()
    return obj

from datetime import timedelta
import httpx
from django.db import transaction
from django.utils import timezone
from .models import *
from .services import record_review
from .ai import generate, redact, build_input, MaterialError, InvalidOutput


def run_ai_once():
    now = timezone.now()
    with transaction.atomic():
        job = ReviewJob.objects.select_for_update().filter(status='pending', next_at__lte=now).order_by('pk').first()
        if not job: return False
        job.status, job.started_at, job.attempts = 'running', now, job.attempts + 1
        job.save()
        run = AIRun.objects.create(job=job, attempt=job.attempts, request={'frozen':redact(job.frozen), 'input':redact(build_input(job))})
    try:
        output, model, request_data, response = generate(job)
        run.actual_model, run.response = model, redact(response)
        record_review(job.submission_id, 'ai', output, model)
        job.status = 'done'
    except Exception as exc:
        run.error = redact(str(exc))
        if isinstance(exc, MaterialError): job.public_error = str(exc)[:1200]
        elif isinstance(exc, PermissionError): job.public_error = 'AI 服务认证或配置不可用，请家长审核。'
        elif isinstance(exc, InvalidOutput): job.public_error = 'AI 返回的内容未通过结构校验，请家长审核。'
        elif isinstance(exc, httpx.HTTPStatusError):
            code=exc.response.status_code
            job.public_error = 'AI 服务认证失败，请家长审核。' if code in (401,403) else 'AI 服务额度或限流限制，请家长审核。' if code in (402,429) else 'AI 服务调用失败，请家长审核。'
        else: job.public_error = 'AI 暂时无法完成审核，请家长审核。'
        if hasattr(exc, 'raw_response'):
            run.response = redact(exc.raw_response)
            run.actual_model = exc.actual_model
        elif isinstance(exc, httpx.HTTPStatusError):
            run.response = redact(exc.response.text)
        transient = isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)) or (isinstance(exc, httpx.HTTPStatusError) and (exc.response.status_code == 429 or exc.response.status_code >= 500))
        if transient and job.attempts < 3:
            job.status = 'pending'
            job.next_at = timezone.now() + timedelta(seconds=30 * 2 ** (job.attempts-1))
        else:
            job.status = 'parent'
            # Error is not a grade. Subsequent submissions remain eligible for AI.
    run.save()
    job.save(update_fields=['status','next_at','public_error'])
    return True


def maintenance():
    # A crashed process may have sent a request: never blindly replay it.
    stale = timezone.now() - timedelta(minutes=10)
    ReviewJob.objects.filter(status='running', started_at__lt=stale).update(status='parent',public_error='审核工作进程中断，已转交家长，请确认当前版本。')

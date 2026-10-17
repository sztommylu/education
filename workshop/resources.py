import io
import re
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, StreamingHttpResponse, HttpResponse
from django.utils import timezone
from django.utils.http import content_disposition_header
from .models import Asset, Task, SiteConfig, Animation
from .services import require_access, lock_wallet

MAX_SIZE = 50 * 1024 * 1024
ALLOWED = {'application/pdf', 'text/plain', 'text/csv', 'application/msword', 'application/vnd.ms-excel', 'application/vnd.ms-powerpoint', 'application/rtf', 'text/rtf'}
OFFICE = {'word/': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'xl/': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'ppt/': 'application/vnd.openxmlformats-officedocument.presentationml.presentation'}

def sniff(upload):
    import magic
    if not 0 < upload.size <= MAX_SIZE: raise ValidationError('附件大小必须在 1 字节至 50 MB 之间')
    sample = upload.read(8192)
    upload.seek(0)
    mime = magic.from_buffer(sample, mime=True)
    if mime in ('application/zip', 'application/octet-stream') and sample[:2] == b'PK':
        try:
            with zipfile.ZipFile(upload) as z:
                names = z.namelist()
                if len(names) > 3000 or sum(i.file_size for i in z.infolist()) > 100 * 1024 * 1024: raise ValidationError('压缩文档内容过大')
                if any('vbaProject' in n for n in names): raise ValidationError('不接受带宏的办公文档')
                for prefix, typ in OFFICE.items():
                    if '[Content_Types].xml' in names and any(n.startswith(prefix) for n in names): mime = typ; break
        except zipfile.BadZipFile: raise ValidationError('办公文档损坏')
        finally: upload.seek(0)
    if mime not in ALLOWED and mime not in OFFICE.values() and not mime.startswith(('image/', 'audio/', 'video/')): raise ValidationError('不支持该文件内容类型')
    if mime in ('image/svg+xml', 'text/html'): raise ValidationError('不接受可执行内容')
    return mime

@transaction.atomic
def upload_asset(actor, upload, task=None, purpose='result', category='attachment', child=None):
    if task:
        require_access(actor, task.child)
        lock_wallet(task.child)
        task = Task.objects.select_for_update().get(pk=task.pk)
        if task.closed or task.deleted: raise ValidationError('任务已关闭')
    elif not actor.is_staff:
        raise PermissionDenied
    if purpose not in ('requirements', 'result'): raise ValidationError('附件用途无效')
    mime = sniff(upload)
    name = Path(upload.name).name[:200]
    upload.name = str(uuid.uuid4())
    return Asset.objects.create(task=task, category=category, purpose=purpose, name=name, file=upload, mime=mime, size=upload.size)

def asset_response(request, asset):
    if asset.task_id:
        require_access(request.user, asset.task.child)
    elif not request.user.is_staff:
        referenced = SiteConfig.objects.filter(logo=asset).exists() or Animation.objects.filter(asset=asset, enabled=True).exists()
        if not referenced: raise PermissionDenied
    # Resource IDs only; no client paths. Immutable content is always served from private storage.
    size = asset.file.size
    mime = asset.mime
    download = 'download' in request.GET or (not mime.startswith(('image/', 'audio/', 'video/', 'text/plain')) and mime != 'application/pdf')
    range_header = request.headers.get('Range')
    if range_header:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header)
        if not match or not any(match.groups()):
            response = HttpResponse(status=416)
            response['Content-Range'] = f'bytes */{size}'
            return response
        a,b = match.groups()
        start = int(a) if a else max(0, size - int(b))
        end = min(int(b), size-1) if a and b else size-1
        if start >= size or start > end:
            response = HttpResponse(status=416); response['Content-Range'] = f'bytes */{size}'; return response
        def chunks():
            with asset.file.open('rb') as f:
                f.seek(start)
                remaining = end-start+1
                while remaining:
                    data = f.read(min(65536, remaining))
                    if not data: break
                    remaining -= len(data)
                    yield data
        response = StreamingHttpResponse(chunks(), status=206, content_type=mime)
        response['Content-Range'] = f'bytes {start}-{end}/{size}'
        response['Content-Length'] = end-start+1
    else:
        response = FileResponse(asset.file.open('rb'), content_type=mime)
    response['Content-Disposition'] = content_disposition_header(download, asset.name)
    response['Accept-Ranges'] = 'bytes'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-store'
    return response

def cleanup_resources():
    deleted = 0
    cutoff = timezone.now() - timedelta(hours=1)
    with transaction.atomic():
        for asset in Asset.objects.select_for_update().filter(created_at__lt=cutoff):
            # Active drafts, formal versions and all branding references are retained.
            if asset.task_id and asset.draft_active and not asset.task.deleted: continue
            if asset.submission_set.exists() or SiteConfig.objects.filter(logo=asset).exists() or Animation.objects.filter(asset=asset).exists(): continue
            file = asset.file
            asset.delete()
            transaction.on_commit(file.delete)
            deleted += 1
    # Files left by rolled-back uploads are untracked and must also age one hour.
    tracked = set(Asset.objects.values_list('file', flat=True))
    private = Path(settings.MEDIA_ROOT) / 'private'
    if private.exists():
        for path in private.rglob('*'):
            if path.is_file() and path.relative_to(Path(settings.MEDIA_ROOT)).as_posix() not in tracked and path.stat().st_mtime < cutoff.timestamp():
                path.unlink(); deleted += 1
    return deleted

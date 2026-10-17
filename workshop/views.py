import hashlib
import random
import uuid
from datetime import timedelta
from functools import wraps
from urllib.parse import urlencode
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Case, When, F, DateField
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET
from .models import *
from .forms import *
from .services import *
from .resources import upload_asset, asset_response, cleanup_resources
from .dictation import questions as dictation_questions, parse_strokes, draft_state, completion_status


def brand_context(request):
    config=SiteConfig.load()
    animations=Animation.objects.filter(enabled=True).select_related('asset')
    if config.animation_theme: animations=animations.filter(Q(theme='')|Q(theme=config.animation_theme))
    return {'brand': config, 'animation_data': [{'name':a.name, 'scene':a.scene, 'theme':a.theme, 'url':f'/assets/{a.asset_id}/', 'mime':a.asset.mime} for a in animations]}

def page(qs, request, key='page'): return Paginator(qs,10).get_page(request.GET.get(key))

def authorized_child(request):
    if request.user.is_parent:
        children = User.objects.filter(parent_links__parent=request.user, is_student=True).order_by('pk')
        if not children.exists(): return None, children
        requested_child = request.GET.get('child')
        if request.method == 'POST':
            requested_child = request.POST.get('child') or requested_child
        child = get_object_or_404(children, pk=requested_child or children.first().pk)
        return child, children
    if request.user.is_student: return request.user, User.objects.filter(pk=request.user.pk)
    return None, User.objects.none()

def business(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not (request.user.is_parent or request.user.is_student):
            return render(request,'workshop/pending.html',status=403)
        return view(request,*args,**kwargs)
    return wrapped

def error_message(request, exc):
    messages.error(request, '；'.join(exc.messages) if isinstance(exc, ValidationError) else str(exc))

def sign_in(request):
    if request.user.is_authenticated: return redirect('home')
    form=AuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST':
        key=hashlib.sha256((request.META.get('REMOTE_ADDR','')+'|'+request.POST.get('username','').lower()).encode()).hexdigest()
        with transaction.atomic():
            attempt,_=LoginAttempt.objects.get_or_create(key=key)
            attempt=LoginAttempt.objects.select_for_update().get(pk=attempt.pk)
            if attempt.window < timezone.now()-timedelta(minutes=15): attempt.count=0; attempt.window=timezone.now()
            blocked=attempt.count>=10
            attempt.count+=1; attempt.save()
        if blocked: form.add_error(None,'登录尝试过多，请 15 分钟后再试')
        elif form.is_valid():
            user=form.get_user(); login(request,user); request.session['epoch']=user.session_epoch
            request.session.set_expiry(timezone.now()+timedelta(days=7))
            LoginAttempt.objects.filter(key=key).delete()
            return redirect('home')
    return render(request,'workshop/login.html',{'form':form,'oidc':bool(settings.OIDC_ISSUER)})

@login_required
@require_POST
def sign_out(request):
    logout(request); return redirect('login')

@login_required
def password_change(request):
    form=PasswordChangeForm(request.user,request.POST or None)
    if request.method=='POST' and form.is_valid():
        form.save(); logout(request); messages.success(request,'密码已更新，所有旧会话已失效，请重新登录'); return redirect('login')
    return render(request,'workshop/form.html',{'form':form,'title':'修改密码'})

@login_required
def home(request):
    if request.user.is_staff and not(request.user.is_student or request.user.is_parent): return redirect('/admin/')
    if not(request.user.is_student or request.user.is_parent): return render(request,'workshop/pending.html',status=403)
    child,children=authorized_child(request)
    if not child: return render(request,'workshop/pending.html',{'parent_missing':True})
    if request.user.is_parent:
        return redirect(reverse('task_create') + f'?child={child.pk}')
    w=Wallet.objects.get_or_create(child=child)[0]
    daily=Task.objects.filter(child=child,deleted=False,day=today(),kind='daily')
    done=daily.filter(phase='awarded').count()
    total=daily.count()
    tasks=focus_query(child).filter(phase__in=['draft','waiting','revision','passed'])[:5]
    notifications=Notification.objects.filter(child=request.user,acknowledged_at__isnull=True).order_by('pk')[:20] if request.user.is_student else []
    choices=SiteConfig.load().encouragements or ['每一小步，都在向上生长。','认真完成今天的小事，就是了不起的进步。','不必一步到位，只要比昨天更进一步。']
    return render(request,'workshop/home.html',{'child':child,'children':children,'wallet':w,'done':done,'total':total,'progress':round(done/total*100) if total else 0,'tasks':tasks,'notifications':notifications,'encouragement':random.choice(choices),'pending_count':Task.objects.filter(child=child,deleted=False,phase__in=['waiting','passed']).count(),'today':today()})

def focus_query(child,archive=False):
    day=today()
    closed=Q(phase__in=['awarded','rejected'])
    old=Q(kind='daily',day__lt=day)|Q(kind='long',due_date__lt=day)|Q(kind='long',due_date__isnull=True)
    q=Task.objects.filter(child=child,deleted=False).annotate(sort_date=Case(When(kind='daily',then=F('day')),default=F('due_date'),output_field=DateField()))
    if archive: return q.filter(closed&old).order_by(F('sort_date').desc(nulls_last=True),'-pk')
    return q.exclude(closed&old).order_by(F('sort_date').asc(nulls_last=True),'pk')

@business
def task_list(request):
    child,children=authorized_child(request)
    if not child: return redirect('home')
    archive=request.GET.get('view')=='archive'
    tasks=focus_query(child,archive)
    phase=request.GET.get('phase','')
    if phase in dict(Task.PHASES): tasks=tasks.filter(phase=phase)
    return render(request,'workshop/tasks.html',{'child':child,'children':children,'tasks':page(tasks,request),'archive':archive,'phase':phase,'submit_animation':request.session.pop('submitted_animation',False)})

@login_required
@require_GET
def task_reviews(request):
    if not request.user.is_parent:
        raise PermissionDenied('任务审核仅对关联家长开放')
    return HttpResponse('家长端任务审核页面已移除。', status=410, content_type='text/plain; charset=utf-8')

@business
def task_create(request):
    if not request.user.is_parent:
        raise PermissionDenied('任务由家长布置，学生不能创建任务')
    child,children=authorized_child(request)
    if not child: return redirect('home')
    form=ParentTaskForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        task=create_task(request.user,child,**form.task_data())
        messages.success(request,'任务已布置，等待孩子完成并提交'); return redirect('task_detail',pk=task.pk)
    selected_course = next((subject.name for subject in form.fields['subject'].queryset if str(subject.pk) == str(form['subject'].value())), '')
    return render(request,'workshop/assign_task.html',{'form':form,'title':'布置任务','submit_label':'布置任务','child':child,'children':children,'selected_course':selected_course})

@business
@require_POST
def task_remove(request, pk):
    task = get_object_or_404(Task, pk=pk, deleted=False)
    require_access(request.user, task.child, parent=True)
    try:
        delete_task(request.user, pk)
        messages.success(request, '未提交任务已删除')
    except ValidationError as exc:
        error_message(request, exc)
    params = {'child': task.child_id}
    for key in ('phase', 'page'):
        if request.GET.get(key):
            params[key] = request.GET[key]
    return redirect(reverse('tasks') + '?' + urlencode(params))


@business
@require_POST
def task_decision(request, pk):
    task = get_object_or_404(Task, pk=pk, deleted=False)
    require_access(request.user, task.child, parent=True)
    try:
        version = int(request.POST.get('version', ''))
        decision = request.POST.get('decision', '')
        settlement = decide_task(request.user, pk, version, decision)
        completed_message = f'任务已完成，积分到账 +{settlement.points}。' if settlement and settlement.points else '任务已完成。'
        messages.success(request, '已退回，等待学生修改后重新提交。' if decision == 'revision' else completed_message)
    except (ValueError, ValidationError) as exc:
        error_message(request, exc if isinstance(exc, ValidationError) else ValidationError('提交版本无效，请刷新页面'))
    if request.POST.get('return_to') == 'detail':
        return redirect('task_detail', pk=pk)
    params = {'child': task.child_id}
    for key in ('phase', 'page'):
        if request.GET.get(key):
            params[key] = request.GET[key]
    return redirect(reverse('tasks') + '?' + urlencode(params))


@business
def task_edit(request, pk):
    task = get_object_or_404(Task, pk=pk)
    require_access(request.user, task.child, parent=True)
    if task.deleted:
        return render(request, 'workshop/task_deleted.html', {'child':task.child}, status=410)
    if task.closed:
        raise PermissionDenied('已完结任务不能编辑')
    assignment = task.template_snapshot.get('assignment', {})
    initial = {'title':task.title, 'subject':task.subject_id, 'kind':task.kind,
               'due_date':task.due_date, 'requirements':task.requirements,
               'english_category':assignment.get('category', ''), 'version':task_edit_version(task),
               **assignment.get('content', {})}
    form = ParentTaskEditForm(request.POST or None, task=task, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            edit_task(request.user, task.pk, form.cleaned_data['version'], **form.task_data())
            messages.success(request, '任务已修改。')
            return redirect('task_detail', pk=pk)
        except ValidationError as exc:
            form.add_error(None, exc)
    return render(request, 'workshop/assign_task.html', {
        'form':form, 'child':task.child, 'task':task, 'editing':True,
        'selected_course':task.subject.name,
    })


@business
def task_detail(request,pk):
    task=get_object_or_404(Task,pk=pk)
    require_access(request.user,task.child)
    if task.deleted:
        if request.method == 'POST' and request.headers.get('Accept') == 'application/json':
            return JsonResponse({'error':'任务已被删除'}, status=410)
        return render(request, 'workshop/task_deleted.html', {'child':task.child}, status=410)
    if request.user.is_parent and request.GET.get('review') == '1':
        return redirect('task_detail', pk=pk)
    reviewing_submission = False
    detail_url = reverse('task_detail',args=[pk]) + ('?review=1' if reviewing_submission else '')
    student_submission = request.user.is_student and not request.user.is_parent
    dictation_items = dictation_questions(task)
    assignment_category = task.template_snapshot.get('assignment', {}).get('category')
    translation_task = task.subject.name == '英语' and assignment_category == 'translation'
    handwriting_task = task.subject.name == '英语' and assignment_category in ('vocabulary', 'translation')
    parent_handwriting_view = request.user.is_parent and handwriting_task
    if parent_handwriting_view:
        form = None
    elif student_submission and handwriting_task:
        legacy_handwriting_post = request.method == 'POST' and request.POST.get('operation', request.POST.get('action', 'save')) in ('save_handwriting', 'save', 'submit')
        form=HandwritingSubmissionForm(request.POST if legacy_handwriting_post else None)
    elif student_submission:
        form=StudentSubmissionForm(request.POST or None,request.FILES or None)
    else:
        form=DraftForm(request.POST or None,request.FILES or None,task=task,initial={'requirements':task.requirements,'result':task.result})
    if request.method=='POST':
        try:
            action=request.POST.get('operation',request.POST.get('action','save'))
            if parent_handwriting_view and action in ('save', 'submit', 'save_handwriting_answer', 'submit_handwriting_draft'):
                raise PermissionDenied('英语任务由学生手写提交，家长可查看和审核。')
            if action == 'save_handwriting_answer':
                try:
                    index = int(request.POST.get('index', ''))
                    revision = int(request.POST.get('revision', ''))
                    saved = save_handwriting_answer(request.user, pk, index, request.POST.get('handwriting', ''), revision)
                    return JsonResponse(saved)
                except (ValueError, ValidationError) as exc:
                    error = '题号或版本无效，请刷新页面。' if isinstance(exc, ValueError) else '；'.join(exc.messages)
                    return JsonResponse({'error':error}, status=400)
            elif student_submission and handwriting_task and action == 'submit_handwriting_draft':
                try:
                    revision = int(request.POST.get('revision', ''))
                except ValueError:
                    raise ValidationError('答案版本无效，请刷新页面。')
                submit_handwriting_draft(request.user, pk, revision)
                messages.success(request, '全部手写答案已保存，已提交 AI 批改。')
                return redirect(detail_url)
            elif student_submission and handwriting_task and action in ('save_handwriting', 'save', 'submit'):
                if request.FILES:
                    raise ValidationError('英语任务请直接手写答案，无需上传附件。')
                if form.is_valid():
                    save_handwriting_and_submit(request.user, pk, form.cleaned_data['handwriting'])
                    messages.success(request, '手写答案已保存，已提交 AI 批改。')
                    return redirect(detail_url)
            elif action in ('save','submit') and form.is_valid():
                with transaction.atomic():
                    if student_submission:
                        saved_task = save_draft(request.user,pk)
                        purpose = 'result'
                    else:
                        saved_task = save_draft(request.user,pk,form.cleaned_data['requirements'],form.cleaned_data['result'],form.cleaned_data['remove'])
                        purpose = form.cleaned_data['purpose']
                    if form.cleaned_data.get('attachment'): upload_asset(request.user,form.cleaned_data['attachment'],saved_task,purpose)
                    if action=='submit':
                        submit_task(request.user,pk)
                        request.session['submitted_animation']=True
                messages.success(request,'正式提交成功，新的版本已保存' if action=='submit' else '草稿已保存')
                return redirect('tasks') if action=='submit' else redirect(detail_url)
            elif action in ('final', 'review'):
                raise PermissionDenied('家长端任务审核功能已移除')
            elif action=='delete':
                delete_task(request.user,pk); messages.success(request,'未提交任务已删除'); return redirect('tasks')
        except ValidationError as exc: error_message(request,exc)
    review=getattr(task.current,'review',None) if task.current else None
    current_job=ReviewJob.objects.filter(submission=task.current).first() if task.current else None
    fallback=bool(task.current and not review and (not current_job or current_job.status=='parent'))
    handwriting_rows = task.handwriting.get('rows', [])
    if student_submission and handwriting_task and request.method == 'POST':
        try:
            handwriting_rows = parse_strokes(request.POST.get('handwriting', ''), len(dictation_items))
        except ValidationError:
            pass
    handwriting_pending = bool(handwriting_task and task.phase != 'revision' and current_job and current_job.status in ('pending', 'running'))
    translation_submitted = bool(translation_task and task.current_id and task.handwriting)
    translation_pending = bool(translation_task and handwriting_pending)
    return render(request,'workshop/detail.html',{'task':task,'child':task.child,'form':form,'review_form':ReviewForm(initial={'version':task.current_id}),'fallback':fallback,'current_job':current_job,'versions':task.versions.order_by('-number').prefetch_related('attachments'),'feedback':task.final_feedback.filter(submission_id=task.current_id, action='return').order_by('-pk') if task.phase == 'revision' else task.final_feedback.none(),
        'reviewing_submission':reviewing_submission,
        'student_submission':student_submission,
        'handwriting_task':handwriting_task,
        'translation_task':translation_task,
        'translation_source':task.template_snapshot.get('assignment', {}).get('content', {}).get('textbook_text', ''),
        'translation_pending':translation_pending,
        'translation_review':review if translation_submitted else None,
        'parent_handwriting_view':parent_handwriting_view,
        'completion':completion_status(task, current_job, review) if parent_handwriting_view and not reviewing_submission else None,
        'completion_review':review,
        'dictation_items':[{'prompt':item['prompt'], 'label':item['label']} for item in dictation_items],
        'handwriting_rows':handwriting_rows,
        'handwriting_state':draft_state(task),
        'handwriting_practice':student_submission and handwriting_task and not task.closed and not reviewing_submission and not handwriting_pending,
        'handwriting_pending':handwriting_pending,
        'display_handwriting':task.current.snapshot.get('dictation', {}) if reviewing_submission and task.current else task.handwriting,
        'display_requirements':task.current.requirements if reviewing_submission and task.current else task.requirements,
        'display_result':task.current.result if reviewing_submission and task.current else task.result,
        'display_template_requirements':task.current.snapshot.get('template',{}).get('requirements','') if reviewing_submission and task.current else task.template_snapshot.get('requirements',''),
        'display_assets':task.current.attachments.all() if reviewing_submission and task.current else task.assets.all(),
    })

@business
def wallet(request):
    child,children=authorized_child(request)
    if not child: return redirect('home')
    w=Wallet.objects.get_or_create(child=child)[0]
    form=PointsWithdrawalForm(request.POST or None,initial={'request_key':uuid.uuid4()})
    if request.method=='POST':
        try:
            if request.POST.get('action')=='apply' and form.is_valid():
                request_withdrawal(request.user,form.cleaned_data['amount']*100,form.cleaned_data['request_key'])
                messages.success(request,'提现申请已提交，对应积分已冻结'); return redirect('wallet')
            elif request.POST.get('action') in ('cancel','pay','reject'):
                get_object_or_404(Withdrawal,pk=request.POST.get('withdrawal'),child=child)
                handle_withdrawal(request.user,request.POST.get('withdrawal'),request.POST['action'])
                messages.success(request,'提现状态已更新'); return redirect(request.get_full_path())
        except ValidationError as exc: error_message(request,exc)
    return render(request,'workshop/wallet.html',{'child':child,'children':children,'wallet':w,'form':form,'withdrawals':page(Withdrawal.objects.filter(child=child).order_by('-pk'),request)})

@business
@require_GET
def points(request):
    child, children = authorized_child(request)
    if not child:
        return redirect('home')
    wallet = Wallet.objects.get_or_create(child=child)[0]
    entries = PointEntry.objects.filter(child=child).select_related('task__settlement', 'extra_benefit').order_by('-created_at', '-pk')
    return render(request, 'workshop/points.html', {
        'child': child, 'children': children, 'wallet': wallet, 'entries': page(entries, request),
    })


@business
def analysis(request):
    child,children=authorized_child(request)
    if not child: return redirect('home')
    require_access(request.user,child,parent=True)
    if request.method=='POST':
        get_object_or_404(AnalysisRecord,pk=request.POST.get('record'),child=child).delete()
        messages.success(request,'该分析记录已删除'); return redirect(request.get_full_path())
    records=AnalysisRecord.objects.filter(child=child).select_related('submission__task','review').order_by('-created_at','-pk')
    if request.GET.get('kind') in ('knowledge','mistake'): records=records.filter(kind=request.GET['kind'])
    return render(request,'workshop/analysis.html',{'child':child,'children':children,'records':page(records,request)})

@business
def management(request,section='rules'):
    child,children=authorized_child(request)
    if not child: return redirect('home')
    require_access(request.user,child,parent=True)
    if section == 'templates':
        return HttpResponse('该配置功能已移除。', status=410, content_type='text/plain; charset=utf-8')
    model,formclass={'rules':(SubjectRule,RuleForm)}.get(section,(None,None))
    if not model: raise PermissionDenied
    instance=get_object_or_404(model,pk=request.GET.get('edit'),child=child) if request.GET.get('edit') else None
    form=formclass(request.POST or None,request.FILES or None,instance=instance)
    if request.method=='POST':
        try:
            if request.POST.get('action')=='delete':
                obj=get_object_or_404(model,pk=request.POST.get('item'),child=child)
                obj.delete(); return redirect(request.path+'?child='+str(child.pk))
            if form.is_valid():
                with transaction.atomic():
                    lock_wallet(child)
                    obj=form.save(commit=False); obj.child=child
                    obj.full_clean()
                    obj.save()
                messages.success(request,'配置已保存'); return redirect(request.path+'?child='+str(child.pk))
        except ValidationError as exc: error_message(request,exc)
    return render(request,'workshop/manage.html',{'child':child,'children':children,'section':section,'form':form,'items':page(model.objects.filter(child=child).order_by('-pk'),request)})

@business
def benefits(request):
    child,children=authorized_child(request)
    if not child: return redirect('home')
    require_access(request.user,child,parent=True)
    form = ExtraBenefitForm(request.POST or None, initial={'request_key':uuid.uuid4()})
    if request.method == 'POST' and form.is_valid():
        try:
            grant = grant_extra_benefit(request.user, child, **form.cleaned_data)
            messages.success(request, f'额外福利已发放，{child}积分到账 +{grant.points}。')
            return redirect(reverse('benefits') + '?' + urlencode({'child':child.pk}))
        except ValidationError as exc:
            form.add_error(None, exc)
    grants = ExtraBenefitGrant.objects.filter(child=child).select_related('parent').order_by('-created_at','-pk')
    wallet = Wallet.objects.get_or_create(child=child)[0]
    return render(request, 'workshop/benefits.html', {
        'child':child, 'children':children, 'form':form, 'grants':page(grants,request), 'wallet':wallet,
    })

@business
def records(request):
    child,children=authorized_child(request)
    if not child: return redirect('home')
    rows=[{'time':x.created_at,'title':x.task.title,'label':'任务奖励','detail':f'积分 +{x.points}'} for x in Settlement.objects.filter(task__child=child).select_related('task')]
    rows += [{'time':x.created_at,'title':x.reason,'label':'额外福利','detail':f'积分 +{x.points}'} for x in ExtraBenefitGrant.objects.filter(child=child)]
    return render(request,'workshop/records.html',{'child':child,'children':children,'records':page(sorted(rows,key=lambda r:r['time'],reverse=True),request)})

@login_required
def asset(request,pk):
    obj=get_object_or_404(Asset,pk=pk)
    return asset_response(request,obj)

@login_required
@require_POST
def acknowledge(request,pk):
    if not request.user.is_student: raise PermissionDenied
    Notification.objects.filter(pk=pk,child=request.user,acknowledged_at__isnull=True).update(acknowledged_at=timezone.now())
    return JsonResponse({'ok':True})

@login_required
@require_POST
def motion(request):
    User.objects.filter(pk=request.user.pk).update(reduce_motion=request.POST.get('off')=='1')
    return redirect('home')

@login_required
def site_settings(request):
    if not request.user.is_staff: raise PermissionDenied
    config=SiteConfig.load()
    form=SiteConfigForm(request.POST or None,request.FILES or None,instance=config)
    if request.method=='POST':
        if request.POST.get('action') in ('toggle_animation','delete_animation'):
            anim=get_object_or_404(Animation,pk=request.POST.get('animation'))
            if request.POST['action']=='delete_animation': anim.delete()
            else: anim.enabled=not anim.enabled; anim.save(update_fields=['enabled'])
            return redirect('site_settings')
        if request.POST.get('action')=='animation':
            try:
                if not request.FILES.get('material') or not request.POST.get('animation_name','').strip(): raise ValidationError('请填写名称并选择素材')
                if request.POST.get('scene') != 'submit': raise ValidationError('无效动效场景')
                with transaction.atomic():
                    material=upload_asset(request.user,request.FILES['material'],category='animation')
                    if not material.mime.startswith(('image/','video/')): raise ValidationError('动效仅支持图片或视频')
                    Animation.objects.create(name=request.POST['animation_name'][:80],scene=request.POST['scene'],theme=request.POST.get('theme','')[:40],asset=material)
                messages.success(request,'动效已上传'); return redirect('site_settings')
            except ValidationError as exc: error_message(request,exc)
        if request.POST.get('action')=='cleanup':
            count=cleanup_resources(); messages.success(request,f'已清理 {count} 个超过一小时且无引用的资源'); return redirect('site_settings')
        if form.is_valid():
            try:
                with transaction.atomic():
                    obj=form.save(commit=False)
                    if form.cleaned_data.get('logo_upload'):
                        img=upload_asset(request.user,form.cleaned_data['logo_upload'],category='brand')
                        if not img.mime.startswith('image/'): raise ValidationError('Logo 必须是图片')
                        obj.logo=img
                    obj.save()
                messages.success(request,'站点设置已保存'); return redirect('site_settings')
            except ValidationError as exc: error_message(request,exc)
    return render(request,'workshop/settings.html',{'form':form,'animations':Animation.objects.select_related('asset').order_by('-pk')[:50]})

def oidc_client():
    from authlib.integrations.django_client import OAuth
    if not settings.OIDC_ISSUER: raise PermissionDenied('OIDC 未启用')
    oauth=OAuth()
    return oauth.register('family',client_id=settings.OIDC_CLIENT_ID,client_secret=settings.OIDC_CLIENT_SECRET,server_metadata_url=settings.OIDC_ISSUER.rstrip('/')+'/.well-known/openid-configuration',client_kwargs={'scope':'openid profile','code_challenge_method':'S256'})

def oidc_login(request):
    return oidc_client().authorize_redirect(request,request.build_absolute_uri('/oidc/callback/'))

def oidc_callback(request):
    try:
        token=oidc_client().authorize_access_token(request)
        info=token.get('userinfo')
        if not info or info.get('iss')!=settings.OIDC_ISSUER or not info.get('sub'): raise PermissionDenied('外部身份校验失败')
        with transaction.atomic():
            identity=Identity.objects.select_related('user').filter(issuer=info['iss'],subject=info['sub']).first()
            if identity: user=identity.user
            else:
                user=User.objects.create_user(username='oidc_'+uuid.uuid4().hex,password=None)
                Identity.objects.create(user=user,issuer=info['iss'],subject=info['sub']); user.refresh_from_db()
        if not user.is_active: raise PermissionDenied('账号已停用')
        login(request,user,backend='django.contrib.auth.backends.ModelBackend')
        request.session['epoch']=user.session_epoch; request.session.set_expiry(timezone.now()+timedelta(days=7))
        return redirect('home')
    except PermissionDenied: raise
    except Exception:
        messages.error(request,'外部登录失败，请重新开始登录'); return redirect('login')

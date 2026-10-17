from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from workshop.models import *
from workshop.services import *
class Command(BaseCommand):
    help='建立独立的本地演示家庭；不会覆盖已存在账号或奖励。必须显式提供密码。'
    def add_arguments(self,parser): parser.add_argument('--password',required=True)
    @transaction.atomic
    def handle(self,*args,**options):
        if not settings.DEBUG: raise CommandError('仅允许在 DEBUG=1 的开发环境生成演示数据')
        password=options['password']
        from django.contrib.auth.password_validation import validate_password
        validate_password(password)
        if User.objects.filter(username__in=['xiaoke','parent','admin']).exists(): raise CommandError('演示账号已存在，已停止，避免覆盖数据')
        child=User.objects.create_user('xiaoke',password=password,first_name='小禾',is_student=True)
        parent=User.objects.create_user('parent',password=password,first_name='禾妈妈',is_parent=True)
        User.objects.create_superuser('admin',password=password,first_name='管理员')
        ParentChild.objects.create(parent=parent,child=child)
        config=SiteConfig.load(); config.encouragements=['把今天的小努力，种成明天的小惊喜。','你认真思考的样子，就已经很棒了。']; config.save()
        math,_=Subject.objects.get_or_create(name='数学',defaults={'icon':'📐'})
        chinese,_=Subject.objects.get_or_create(name='语文',defaults={'icon':'📖'})
        english,_=Subject.objects.get_or_create(name='英语',defaults={'icon':'🔤'})
        science,_=Subject.objects.get_or_create(name='科学',defaults={'icon':'🔬'})
        for subject in (math, chinese, english):
            SubjectRule.objects.create(child=child,subject=subject,prompt='根据该学科的特点评价完成情况。按独立小题去重，不要将同一错误反复计数；区分计算、理解、格式和材料辨认，给出具体鼓励和改进建议。')
        long=create_task(parent,child,'观察一粒种子的生长',science,'long',today()+timedelta(days=7),'播下一粒种子，记录七天的变化，可以用照片或文字。')
        self.stdout.write(self.style.SUCCESS('演示家庭已创建：学生 xiaoke / 家长 parent / 独立管理员 admin；使用你指定的密码。'))

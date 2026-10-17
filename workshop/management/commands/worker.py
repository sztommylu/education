import time
from django.core.management.base import BaseCommand
from workshop.worker import maintenance, run_ai_once
class Command(BaseCommand):
    help = '数据库持久队列；--once 处理一轮，否则持续运行'
    def add_arguments(self, parser): parser.add_argument('--once', action='store_true')
    def handle(self, *args, **options):
        tick = 0
        while True:
            if tick % 12 == 0: maintenance()
            busy = run_ai_once()
            if options['once']:
                if not busy: break
            else: time.sleep(1 if busy else 5)
            tick += 1

from django.apps import AppConfig
class WorkshopConfig(AppConfig):
    name = "workshop"
    verbose_name = "成长工坊"
    def ready(self):
        from . import signals

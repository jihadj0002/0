import sys

from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'

    def ready(self):
        if "test" in sys.argv:
            return
        try:
            from .webhooks import recover_zombie_batches
            recover_zombie_batches()
        except Exception:
            pass
import sys
import threading

from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'

    def ready(self):
        if "test" in sys.argv:
            return
        self._recover_zombies()

    def _recover_zombies(self):
        def run():
            from .webhooks import recover_zombie_batches
            recover_zombie_batches()
        threading.Thread(target=run, daemon=True).start()

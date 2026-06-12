import threading

from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'

    def ready(self):
        threading.Thread(
            target=self._recover_zombies,
            daemon=True,
        ).start()

    def _recover_zombies(self):
        from .webhooks import recover_zombie_batches
        recover_zombie_batches()

import sys
import threading

from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'

    def ready(self):
        # Only recover zombie batches in real server processes. Under the test
        # runner this would fire the AI pipeline (network calls) against test data.
        if "test" in sys.argv:
            return
        threading.Thread(
            target=self._recover_zombies,
            daemon=True,
        ).start()

    def _recover_zombies(self):
        from .webhooks import recover_zombie_batches
        recover_zombie_batches()

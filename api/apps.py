import sys
import threading

from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'

    def ready(self):
        if "test" in sys.argv:
            return
        # Only the web process hosts the async runtime + zombie sweep. RQ's
        # email worker (CRM campaigns) must NOT start an event loop thread.
        if any("rqworker" in a for a in sys.argv[1:]):
            return
        self._start_async()
        self._recover_zombies()

    def _start_async(self):
        from .concurrency import start_event_loop, schedule_repeating
        start_event_loop()

        # Periodic sweep so unprocessed MessageBatch rows left behind by a
        # crash are re-submitted — nothing waits for a manual restart.
        try:
            from .concurrency import submit_sync

            async def _zombie_sweep():
                from .webhooks import recover_zombie_batches
                submit_sync(recover_zombie_batches)

            schedule_repeating(20, lambda: _zombie_sweep())
        except Exception:
            pass

    def _recover_zombies(self):
        def run():
            from .webhooks import recover_zombie_batches
            recover_zombie_batches()
        threading.Thread(target=run, daemon=True).start()

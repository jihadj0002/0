import subprocess
import sys

from django.apps import AppConfig
from django.core.cache import cache


class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'

    def ready(self):
        if "test" in sys.argv:
            return
        self._recover_zombies()
        self._start_rq_workers()

    def _recover_zombies(self):
        try:
            from .webhooks import recover_zombie_batches
            recover_zombie_batches()
        except Exception:
            pass

    def _start_rq_workers(self):
        if not cache.add("rq_worker_start_lock", True, 120):
            return
        try:
            subprocess.Popen(
                ["python", "manage.py", "rqworker-pool", "default", "email",
                 "--num-workers", "5"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
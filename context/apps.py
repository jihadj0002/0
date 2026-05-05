from django.apps import AppConfig


class ContextConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'context'

    def ready(self):
        import context.signals  # noqa: F401

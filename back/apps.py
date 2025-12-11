from django.apps import AppConfig

class BackConfig(AppConfig):
    name = "back"  # <-- your app name

    def ready(self):
        # Import signals to make sure they are registered
        import back.signals
        
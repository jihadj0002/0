from django.apps import AppConfig


from mongoengine import connect
import os


class BackConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "back"


# Setup MongoDB




class MyAppConfig(AppConfig):
    name = "mongo"

    def ready(self):
        # Read URI from env var or Django settings
        
        mongo_uri = os.environ.get("mongodb+srv://jihadj0002_db_user:p9oaBw3ClpZF69m4@cluster0.fxkjzog.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
        connect(host=mongo_uri, alias="default")   # alias "default" for simplicity
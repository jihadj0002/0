# from django.apps import AppConfig


# from mongoengine import connect
# import os


# class BackConfig(AppConfig):
#     default_auto_field = "django.db.models.BigAutoField"
#     name = "back"


# # Setup MongoDB




# class MyAppConfig(AppConfig):
#     name = "mongo"

#     def ready(self):
#         # Read URI from env var or Django settings
#         db="n8n",
#         mongo_uri = os.environ.get("mongodb+srv://jihadj0002_db_user:p9oaBw3ClpZF69m4@cluster0.fxkjzog.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
#         connect(host=mongo_uri, alias="default")   # alias "default" for simplicity






# back/apps.py
# from django.apps import AppConfig
# from mongoengine import connect

# class BackConfig(AppConfig):
#     default_auto_field = "django.db.models.BigAutoField"
#     name = "back"

#     def ready(self):
#         # Connect to MongoDB when the app is ready
#         connect(
#             db="n8n",
#             host="mongodb+srv://jihadj0002_db_user:p9oaBw3ClpZF69m4@cluster0.fxkjzog.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0",
#             alias="default"
#         )
#         print("✅ Connected to MongoDB successfully")

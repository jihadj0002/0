from django.urls import path

from . import views

app_name = "hiring"

urlpatterns = [
    path("", views.apply, name="apply"),
    path("thanks/", views.thanks, name="thanks"),
]
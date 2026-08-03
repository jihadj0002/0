from django.urls import path

from . import views

app_name = "hiring_admin"

urlpatterns = [
    path("", views.index, name="index"),
    path("export/messages/", views.export_messages, name="export_messages"),
    path("export/csv/", views.export_csv, name="export_csv"),
    path("meetings/", views.meetings, name="meetings"),
    path("meetings/new/", views.meeting_new, name="meeting_new"),
    path("meetings/<int:pk>/status/", views.meeting_status, name="meeting_status"),
    path("<str:uid>/", views.candidate_detail, name="candidate_detail"),
    path("<str:uid>/action/", views.candidate_action, name="candidate_action"),
]
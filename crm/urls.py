from django.urls import path
from . import views

app_name = "crm"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("logout/", views.logout, name="logout"),

    # Leads
    path("leads/", views.leads, name="leads"),
    path("leads/new/", views.lead_new, name="lead_new"),
    path("leads/<int:pk>/", views.lead_detail, name="lead_detail"),
    path("leads/<int:pk>/edit/", views.lead_edit, name="lead_edit"),
    path("leads/<int:pk>/delete/", views.lead_delete, name="lead_delete"),

    # Pipeline
    path("pipeline/", views.pipeline, name="pipeline"),

    # Customers
    path("customers/", views.customers, name="customers"),
    path("customers/<int:pk>/", views.customer_detail, name="customer_detail"),

    # Companies
    path("companies/", views.companies, name="companies"),
    path("companies/<int:pk>/", views.company_detail, name="company_detail"),
    path("companies/new/", views.company_new, name="company_new"),

    # Activities
    path("calls/", views.calls, name="calls"),
    path("demos/", views.demos, name="demos"),
    path("followups/", views.followups, name="followups"),
    path("calendar/", views.calendar, name="calendar"),
    path("tasks/", views.tasks, name="tasks"),
    path("tasks/<int:pk>/delete/", views.task_delete, name="task_delete"),

    # Resources
    path("scripts/", views.scripts, name="scripts"),
    path("scripts/<int:pk>/toggle/", views.script_toggle, name="script_toggle"),
    path("faq/", views.faq, name="faq"),

    # Manage
    path("team/", views.team, name="team"),
    path("reports/", views.reports, name="reports"),
    path("settings/", views.settings, name="settings"),

    # AJAX
    path("ajax/search", views.ajax_search, name="ajax_search"),
    path("ajax/notifications", views.ajax_notifications, name="ajax_notifications"),
    path("ajax/notifications/mark-read", views.ajax_notifications_mark_read, name="ajax_notifications_mark_read"),
    path("ajax/leads/quick-create", views.ajax_quick_create_lead, name="ajax_quick_create_lead"),
    path("ajax/leads/<int:pk>/popup", views.ajax_lead_popup, name="ajax_lead_popup"),
    path("ajax/leads/<int:pk>/update", views.ajax_quick_update, name="ajax_quick_update"),
    path("ajax/leads/<int:pk>/move", views.ajax_kanban_move, name="ajax_kanban_move"),
    path("ajax/leads/<int:pk>/convert", views.ajax_convert_customer, name="ajax_convert_customer"),
    path("ajax/followups/<int:pk>/done", views.ajax_followup_done, name="ajax_followup_done"),
    path("ajax/tasks/<int:pk>/toggle", views.ajax_task_toggle, name="ajax_task_toggle"),
    path("ajax/calls/log", views.ajax_call_log, name="ajax_call_log"),
    path("ajax/meetings/<int:pk>/status", views.ajax_meeting_status, name="ajax_meeting_status"),
    path("ajax/calendar/events", views.ajax_calendar_events, name="ajax_calendar_events"),
]

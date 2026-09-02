from django.urls import path
from . import views
from . import outreach_views

app_name = "crm"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("logout/", views.logout, name="logout"),

    # PWA
    path("manifest.json", views.pwa_manifest, name="pwa_manifest"),
    path("sw.js", views.pwa_sw, name="pwa_sw"),

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
    path("scripts/<int:pk>/edit/", views.script_edit, name="script_edit"),
    path("faq/", views.faq, name="faq"),
    path("learn/", views.learn, name="learn"),
    path("learn/<slug:slug>/", views.learn, name="learn_article"),

    # Manage
    path("team/", views.team, name="team"),
    path("reports/", views.reports, name="reports"),
    path("settings/", views.settings, name="settings"),

    # ============================================================
    # EMAIL OUTREACH (new)
    # ============================================================
    path("outreach/", outreach_views.outreach_overview, name="outreach_overview"),
    path("outreach/campaigns/", outreach_views.outreach_campaigns, name="outreach_campaigns"),
    path("outreach/campaigns/create/", outreach_views.outreach_campaign_create, name="outreach_campaign_create"),
    path("outreach/campaigns/<slug:uid>/", outreach_views.outreach_campaign_detail, name="outreach_campaign_detail"),
    path("outreach/campaigns/<slug:uid>/poll/", outreach_views.outreach_campaign_poll, name="outreach_campaign_poll"),
    path("outreach/campaigns/<slug:uid>/recipients/", outreach_views.outreach_campaign_recipients, name="outreach_campaign_recipients"),
    path("outreach/campaigns/<slug:uid>/settings/", outreach_views.outreach_campaign_settings, name="outreach_campaign_settings"),
    path("outreach/campaigns/<slug:uid>/send/", outreach_views.outreach_campaign_send, name="outreach_campaign_send"),
    path("outreach/campaigns/<slug:uid>/pause/", outreach_views.outreach_campaign_pause, name="outreach_campaign_pause"),
    path("outreach/campaigns/<slug:uid>/archive/", outreach_views.outreach_campaign_archive, name="outreach_campaign_archive"),
    path("outreach/templates/", outreach_views.outreach_templates, name="outreach_templates"),
    path("outreach/templates/new/", outreach_views.outreach_template_new, name="outreach_template_new"),
    path("outreach/templates/<slug:uid>/edit/", outreach_views.outreach_template_edit, name="outreach_template_edit"),
    path("outreach/templates/<slug:uid>/delete/", outreach_views.outreach_template_delete, name="outreach_template_delete"),
    path("outreach/templates/<slug:uid>/preview/", outreach_views.outreach_template_preview, name="outreach_template_preview"),
    path("outreach/accounts/", outreach_views.outreach_accounts, name="outreach_accounts"),
    path("outreach/accounts/connect/", outreach_views.outreach_account_connect, name="outreach_account_connect"),
    path("outreach/accounts/<slug:uid>/edit/", outreach_views.outreach_account_edit, name="outreach_account_edit"),
    path("outreach/accounts/<slug:uid>/delete/", outreach_views.outreach_account_delete, name="outreach_account_delete"),
    path("outreach/accounts/<slug:uid>/test/", outreach_views.outreach_account_test, name="outreach_account_test"),
    path("outreach/analytics/", outreach_views.outreach_analytics, name="outreach_analytics"),
    path("outreach/analytics/data/", outreach_views.outreach_analytics_data, name="outreach_analytics_data"),
    path("outreach/activity/", outreach_views.outreach_activity, name="outreach_activity"),
    path("outreach/batch/<slug:uid>/", outreach_views.outreach_batch_detail, name="outreach_batch_detail"),
    path("outreach/batch/<slug:uid>/poll/", outreach_views.outreach_batch_poll, name="outreach_batch_poll"),
    path("outreach/export/", outreach_views.outreach_export, name="outreach_export"),

    # ============================================================
    # COLD MAIL (legacy — redirects to new outreach/ URLs)
    # ============================================================
    path("cold-mail/", outreach_views.legacy_cold_mail_redirect, name="cold_mail_index"),
    path("cold-mail/select/", outreach_views.legacy_cold_mail_select_redirect, name="cold_mail_select"),
    path("cold-mail/send/", outreach_views.legacy_cold_mail_redirect, name="cold_mail_compose"),
    path("cold-mail/list-templates/", outreach_views.legacy_cold_mail_redirect, name="cold_mail_list_templates"),
    path("cold-mail/batch/<uuid:uid>/", outreach_views.legacy_cold_mail_batch_redirect, name="cold_mail_batch_detail"),
    path("cold-mail/batch/<uuid:uid>/poll/", outreach_views.legacy_cold_mail_batch_poll_redirect, name="cold_mail_batch_poll"),
    path("cold-mail/export/", outreach_views.legacy_cold_mail_redirect, name="cold_mail_export"),

    # AJAX
    path("ajax/search", views.ajax_search, name="ajax_search"),
    path("ajax/notifications", views.ajax_notifications, name="ajax_notifications"),
    path("ajax/notifications/mark-read", views.ajax_notifications_mark_read, name="ajax_notifications_mark_read"),
    path("ajax/leads/quick-create", views.ajax_quick_create_lead, name="ajax_quick_create_lead"),
    path("ajax/leads/analyze-image", views.ajax_analyze_lead_image, name="ajax_analyze_lead_image"),
    path("ajax/leads/create-from-import", views.ajax_create_imported_leads, name="ajax_create_imported_leads"),
    path("ajax/leads/<int:pk>/popup", views.ajax_lead_popup, name="ajax_lead_popup"),
    path("ajax/leads/<int:pk>/update", views.ajax_quick_update, name="ajax_quick_update"),
    path("ajax/leads/<int:pk>/move", views.ajax_kanban_move, name="ajax_kanban_move"),
    path("ajax/leads/<int:pk>/convert", views.ajax_convert_customer, name="ajax_convert_customer"),
    path("ajax/followups/<int:pk>/done", views.ajax_followup_done, name="ajax_followup_done"),
    path("ajax/tasks/<int:pk>/toggle", views.ajax_task_toggle, name="ajax_task_toggle"),
    path("ajax/tasks/<int:pk>/update", views.ajax_task_update, name="ajax_task_update"),
    path("ajax/calls/log", views.ajax_call_log, name="ajax_call_log"),
    path("ajax/meetings/<int:pk>/status", views.ajax_meeting_status, name="ajax_meeting_status"),
    path("ajax/calendar/events", views.ajax_calendar_events, name="ajax_calendar_events"),
]
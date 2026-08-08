from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

app_name = "back"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),

    path("products", views.products, name="products"),
    path("products/add", views.add_product, name="add_product"),
    path("products/<int:pk>/edit/", views.edit_product, name='edit_product'),
    path("products/<int:pk>/delete/", views.delete_product, name="delete_product"),
    path("products/<int:pk>/gallery/<int:img_pk>/delete/", views.delete_gallery_image, name="delete_gallery_image"),
    path("products/import", views.import_products, name="import_products"),
    path("products/export", views.export_products, name="export_products"),

    # Product Sources (Multi-Source Architecture)
    path("sources", views.product_sources, name="product_sources"),
    path("sources/add", views.add_product_source, name="add_product_source"),
    path("sources/<str:sid>/edit", views.edit_product_source, name="edit_product_source"),
    path("sources/<str:sid>/delete", views.delete_product_source, name="delete_product_source"),
    path("sources/<str:sid>/activate", views.activate_product_source, name="activate_product_source"),
    path("sources/<str:sid>/test", views.test_product_source, name="test_product_source"),
    path("sources/<str:sid>/sync", views.sync_product_source, name="sync_product_source"),

    # Package Management
    path("packages", views.packages, name="packages"),
    path("packages/add", views.add_package, name="add_package"),
    path("packages/<int:pk>/edit/", views.edit_package, name='edit_package'),
    path("packages/<int:pk>/delete/", views.delete_package, name="delete_package"),
    # path("products/import", views.import_products, name="import_products"),
    # path("products/export", views.export_products, name="export_products"),
    
    # path("pricing", views.pricing, name="pricing"),
    path("orders", views.orders, name="orders"),
    path("orders/update-status/", views.update_order_status, name="update_order_status"),
    path("oldchats", views.c_dashboard, name="c_dashboard_old"),
    path("chats/disable-all", views.disable_all_bots, name="disable_all_bots"),
    path("chats/enable-all", views.enable_all_bots, name="enable_all_bots"),

    # path("chats", views.c_dashboard_demo, name="c_dashboard"),
    path("chats", views.message_dashboard, name="c_dashboard"),


    
    path("chats/ajax_messages", views.ajax_load_messages, name="ajax_load_messages"),
    path("chats/ajax_conversations", views.ajax_load_conversations, name="ajax_load_conversations"),

    path("stats", views.stats, name="stats"),
    path("options", views.settingss, name="options"),
    
    path('chats/bot-preview', views.bot_preview, name='bot_preview'),
    path('send_message', views.send_message_ajax, name='send_message'),
    path('send_image', views.send_image_ajax, name='send_image'),
    path('send_message_with_image', views.send_message_with_image_ajax, name='send_message_with_image'),
    


    path("chat-metrics/", views.get_chat_metrics, name="chat_metrics"),
    path("order-analytics/", views.get_order_analytics, name="order_analytics"),
    path("sales-analytics/", views.get_sales_analytics, name="sales_analytics"),
    

    # path('webhook-api/', views.webhook_api, name='webhook_api'),

    path("settings/", views.settings_view, name="settings"),
    path("setup/", views.setup_wizard, name="setup"),
    path("billing/", views.billing_dashboard, name="billing"),
    path("tickets", views.tickets_view, name="tickets"),
    path("tickets/data", views.ajax_tickets, name="ajax_tickets"),
    path("tickets/claim", views.ajax_ticket_claim, name="ajax_ticket_claim"),
    path("tickets/resolve", views.ajax_ticket_resolve, name="ajax_ticket_resolve"),
    path("tickets/reopen", views.ajax_ticket_reopen, name="ajax_ticket_reopen"),
    path("ai-debug/", views.ai_debug, name="ai_debug"),
    path("ai-debug/conversations", views.ai_debug_conversations, name="ai_debug_conversations"),
    path("ai-debug/context", views.ai_debug_context, name="ai_debug_context"),
    path("ai-debug/run-tool", views.ai_debug_run_tool, name="ai_debug_run_tool"),
]

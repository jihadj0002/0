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
    path("products/import", views.import_products, name="import_products"),
    path("products/export", views.export_products, name="export_products"),
    
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

    path("chats", views.message_dashboard, name="c_dashboard"),


    
    path("chats/ajax_messages", views.ajax_load_messages, name="ajax_load_messages"),
    path("chats/ajax_conversations", views.ajax_load_conversations, name="ajax_load_conversations"),

    path("stats", views.stats, name="stats"),
    path("options", views.settingss, name="options"),
    
    path('send_message', views.send_message_ajax, name='send_message'),
    path('send_image', views.send_image_ajax, name='send_image'),
    path('send_message_with_image', views.send_message_with_image_ajax, name='send_message_with_image'),
    


    path("chat-metrics/", views.get_chat_metrics, name="chat_metrics"),
    path("order-analytics/", views.get_order_analytics, name="order_analytics"),
    path("sales-analytics/", views.get_sales_analytics, name="sales_analytics"),
    

    # path('webhook-api/', views.webhook_api, name='webhook_api'),

    
    # path("contact", views.contact, name="contact"),
    # path("", views.home, name="home"),
    # path("login", views.login_view, name="login"),
    # path("logout", views.logout_view, name="logout"),
]

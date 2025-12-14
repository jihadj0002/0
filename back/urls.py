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
    
    # path("pricing", views.pricing, name="pricing"),
    path("orders", views.orders, name="orders"),
    path("orders/update-status/", views.update_order_status, name="update_order_status"),
    path("chats", views.c_dashboard, name="c_dashboard"),

    path("stats", views.stats, name="stats"),
    path("options", views.sett, name="options"),
    
    path('send_message', views.send_message_ajax, name='send_message'),
    


    path("order-analytics/", views.get_order_analytics, name="order_analytics"),
    path("sales-analytics/", views.get_sales_analytics, name="sales_analytics"),
    

    # path('webhook-api/', views.webhook_api, name='webhook_api'),

    
    # path("contact", views.contact, name="contact"),
    # path("", views.home, name="home"),
    # path("login", views.login_view, name="login"),
    # path("logout", views.logout_view, name="logout"),
]

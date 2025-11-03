from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

app_name = "back"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("products", views.products, name="products"),
    path("products/add", views.add_product, name="add_product"),
    # path("pricing", views.pricing, name="pricing"),
    path("orders", views.orders, name="orders"),
    path("stats", views.stats, name="stats"),
    path("options", views.sett, name="options"),
    path("chats", views.c_dashboard, name="c_dashboard"),

    path("products/<int:pk>/edit/", views.edit_product, name='edit_product'),
    path("products/<int:pk>/delete/", views.delete_product, name="delete_product"),
    

    path("orders/update-status/", views.update_order_status, name="update_order_status"),

    path("order-analytics/", views.get_order_analytics, name="order_analytics"),
    path("sales-analytics/", views.get_sales_analytics, name="sales_analytics"),

    # path('webhook-api/', views.webhook_api, name='webhook_api'),

    
    # path("contact", views.contact, name="contact"),
    # path("", views.home, name="home"),
    # path("login", views.login_view, name="login"),
    # path("logout", views.logout_view, name="logout"),
]

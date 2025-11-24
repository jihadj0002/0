from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

app_name = "front"

urlpatterns = [
    path("", views.home, name="home"),
    path("contact", views.contact, name="contact"),
    path("form", views.forumm, name="forumm"),
    path("pricing", views.pricing, name="pricing"),
    path("privacy-policy", views.privacy_policy, name="p_policy"),

    # path("products", views.products, name="products"),
    # path("stats", views.stats, name="stats"),
    # path("options", views.sett, name="options"),
    
    # path("dashboard", views.dashboard, name="dashboard"),
    # path("dashboard/chats", views.c_dashboard, name="c_dashboard"),
    
    path("login", views.login_view, name="login"),
    path("signup", views.signup, name="signup"),
    path("logout", views.logout_view, name="logout"),
]

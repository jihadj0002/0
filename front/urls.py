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
    path("terms-and-conditions", views.terms, name="terms"),

    path("login", views.login_view, name="login"),
    path("signup", views.signup, name="signup"),
    path("logout", views.logout_view, name="logout"),

    path("verify-email/<str:username>/", views.verify_email, name="verify_email"),
    path("verify-email/<str:username>/resend/", views.resend_verification, name="resend_verification"),
]
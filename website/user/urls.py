from django.urls import path, include
from . import views


urlpatterns = [
    path("", views.user, name="user"),
    path("login/", views.user_login, name="login"),
    path("signup/", views.user_signup, name="signup"),
    path("logout/", views.user_logout, name="logout"),
    
]

from django.urls import path, include
from . import views


urlpatterns = [
    path("abc/", views.login, name="login"),
    path("def/", views.signup, name="signup")
    
]

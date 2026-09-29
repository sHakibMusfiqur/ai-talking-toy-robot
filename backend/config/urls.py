from django.contrib.auth import views as auth_views
from django.urls import include, path

from core import views as core_views

urlpatterns = [
    path("", core_views.root, name="root"),
    path("health", core_views.health, name="health"),
    path("health/", core_views.health, name="health-compat"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("api/v1/", include("accounts.urls")),
    path("api/v1/", include("devices.urls")),
]

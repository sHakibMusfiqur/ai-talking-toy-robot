from django.contrib.auth import views as auth_views
from django.urls import include, path

from core import views as core_views
from devices import views as devices_views

urlpatterns = [
    path("", core_views.root, name="root"),
    path("health", core_views.health, name="health"),
    path("health/", core_views.health, name="health-compat"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("dashboard/", devices_views.dashboard, name="dashboard"),
    path(
        "dashboard/robots/<str:pk>/",
        devices_views.robot_controller,
        name="dashboard-controller",
    ),
    path("onboarding/", devices_views.onboarding, name="onboarding"),
    path(
        "onboarding/claim/",
        devices_views.onboarding_claim,
        name="onboarding-claim",
    ),
    path("api/v1/", include("accounts.urls")),
    path("api/v1/", include("devices.urls")),
    path("api/v1/", include("conversations.urls")),
]

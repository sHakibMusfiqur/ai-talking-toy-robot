from django.urls import path

from . import views

urlpatterns = [
    path("devices/register/", views.DeviceRegisterView.as_view(), name="api-device-register"),
    path("devices/register", views.DeviceRegisterView.as_view(), name="api-device-register-raw"),
    path("devices/pair/", views.DevicePairView.as_view(), name="api-device-pair"),
    path("devices/pair", views.DevicePairView.as_view(), name="api-device-pair-raw"),
    path("devices/auth/challenge/", views.DeviceChallengeView.as_view(), name="api-device-challenge"),
    path("devices/auth/token/", views.DeviceTokenView.as_view(), name="api-device-token"),
    path("devices/me/", views.DeviceIdentityView.as_view(), name="api-device-me"),
    path("devices/", views.AdminRobotListView.as_view(), name="api-devices"),
    path("devices/<str:pk>/", views.RobotDetailView.as_view(), name="api-robot-detail"),
    path(
        "devices/<str:pk>/command/",
        views.RobotCommandView.as_view(),
        name="api-robot-command",
    ),
    path(
        "devices/<str:pk>/state/",
        views.RobotStateView.as_view(),
        name="api-robot-state",
    ),
    path(
        "devices/<str:pk>/events/",
        views.RobotEventsView.as_view(),
        name="api-robot-events",
    ),
    path(
        "devices/<str:pk>/sensors/",
        views.RobotSensorsView.as_view(),
        name="api-robot-sensors",
    ),
    path(
        "devices/<str:pk>/configuration/",
        views.RobotConfigurationView.as_view(),
        name="api-robot-configuration",
    ),
    path(
        "devices/<str:pk>/summary/",
        views.RobotControllerSummaryView.as_view(),
        name="api-robot-summary",
    ),
]

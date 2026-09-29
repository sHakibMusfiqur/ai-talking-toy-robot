from django.urls import path

from . import views

urlpatterns = [
    path("devices/register/", views.DeviceRegisterView.as_view(), name="api-device-register"),
    path("devices/register", views.DeviceRegisterView.as_view(), name="api-device-register-raw"),
    path("devices/pair/", views.DevicePairView.as_view(), name="api-device-pair"),
    path("devices/pair", views.DevicePairView.as_view(), name="api-device-pair-raw"),
    path("devices/", views.AdminRobotListView.as_view(), name="api-devices"),
    path("devices/<str:pk>/", views.RobotDetailView.as_view(), name="api-robot-detail"),
]

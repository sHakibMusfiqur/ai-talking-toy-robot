from django.urls import path

from . import views

urlpatterns = [
    path("auth/signup/", views.SignupView.as_view(), name="api-signup"),
    path("auth/login/", views.LoginView.as_view(), name="api-login"),
    path("auth/logout/", views.LogoutView.as_view(), name="api-logout"),
    path("children/", views.ChildListCreateView.as_view(), name="api-children"),
    path(
        "children/<str:pk>/",
        views.ChildDetailView.as_view(),
        name="api-child-detail",
    ),
]

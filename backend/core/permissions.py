from rest_framework import permissions


class AuthRequired(permissions.IsAuthenticated):
    pass

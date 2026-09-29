from rest_framework import permissions


class AuthRequired(permissions.IsAuthenticated):
    pass


class IsAdminRole(permissions.BasePermission):
    """Role gate for admin/fleet endpoints (Section 25): authenticated users
    without the admin role get 403; unauthenticated callers get 401 from
    AuthRequired."""

    message = "Admin access required."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == "admin")

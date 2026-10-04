from rest_framework import permissions


class ConversationTurnPermission(permissions.BasePermission):
    

    message = "Authentication required."

    def has_permission(self, request, view):
        user = request.user
        if user is not None and getattr(user, "is_authenticated", False):
            return True
        auth = request.auth
        return isinstance(auth, dict) and "robot" in auth

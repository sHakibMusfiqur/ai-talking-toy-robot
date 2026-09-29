from rest_framework import permissions


class DeviceAuthenticated(permissions.BasePermission):
   

    def has_permission(self, request, view):
        auth = request.auth
        return isinstance(auth, dict) and "robot" in auth

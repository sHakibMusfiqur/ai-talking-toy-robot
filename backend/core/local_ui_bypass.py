from django.conf import settings
from django.contrib.auth import get_user_model

_LOOPBACK_ADDRESSES = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})


def bypass_active(request):
    
    if not getattr(settings, "DEBUG", False):
        return False
    if not getattr(settings, "LOCAL_UI_TEST_BYPASS", False):
        return False
    if request.META.get("REMOTE_ADDR") not in _LOOPBACK_ADDRESSES:
        return False
    if not getattr(settings, "LOCAL_UI_TEST_USER_EMAIL", ""):
        return False
    return True


class LocalUiTestBypassMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/api/"):
            return self.get_response(request)
        if bypass_active(request) and not request.user.is_authenticated:
            user_model = get_user_model()
            user = (
                user_model.objects.filter(
                    email=settings.LOCAL_UI_TEST_USER_EMAIL,
                    role=user_model.ROLE_PARENT,
                    is_active=True,
                )
                .first()
            )
            if user is not None:
                request.user = user
        return self.get_response(request)

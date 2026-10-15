import socket

from django.conf import settings
from django.contrib.auth import get_user_model

_LOOPBACK_ADDRESSES = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})


def _default_gateway():
    # Docker publishes container ports through the compose bridge, so a
    # browser request to 127.0.0.1:8000 arrives inside the container
    # with REMOTE_ADDR = the bridge gateway (e.g. 172.20.0.1), not
    # loopback. Accept that one address (the container's own default
    # route) as a local client; host-run devservers have no
    # /proc/net/route and keep the original loopback-only rule.
    # Residual spoofing risk is local-dev only: it needs raw sockets on
    # the bridge, and the bypass still requires DEBUG + an explicit
    # opt-in + a valid parent e-mail, never applies to /api/, and only
    # grants view access to the server-rendered pages.
    try:
        with open("/proc/net/route", encoding="ascii") as route_file:
            for line in route_file:
                fields = line.split()
                if len(fields) >= 3 and fields[1] == "00000000":
                    packed = bytes.fromhex(fields[2])[::-1]
                    if len(packed) == 4:
                        return socket.inet_ntoa(packed)
    except (OSError, ValueError):
        pass
    return None


def _is_local_client(remote_addr):
    if remote_addr in _LOOPBACK_ADDRESSES:
        return True
    gateway = _default_gateway()
    return gateway is not None and remote_addr == gateway


def bypass_active(request):

    if not getattr(settings, "DEBUG", False):
        return False
    if not getattr(settings, "LOCAL_UI_TEST_BYPASS", False):
        return False
    if not _is_local_client(request.META.get("REMOTE_ADDR")):
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
                # Marker for templates and api.js: this page's identity
                # came from the local bypass, not a real login session.
                request.local_ui_active = True
        return self.get_response(request)

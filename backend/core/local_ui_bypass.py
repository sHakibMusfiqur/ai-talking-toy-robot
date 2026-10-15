import socket

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth import login as auth_login

_LOOPBACK_ADDRESSES = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})

# Only the dashboard surfaces bootstrap a session. Login, static assets,
# API paths and everything else stay untouched so exactly one page load
# rotates the CSRF token (keeping meta tag and cookie in sync).
_BOOTSTRAP_EXACT = frozenset({"/"})
_BOOTSTRAP_PREFIXES = ("/dashboard/",)


def _default_gateway():
    # Docker publishes container ports through the compose bridge, so a
    # browser request to 127.0.0.1:8000 arrives inside the container
    # with REMOTE_ADDR = the bridge gateway (e.g. 172.20.0.1), not
    # loopback. Accept that one address (the container's own default
    # route) as a local client; host-run devservers have no
    # /proc/net/route and keep the original loopback-only rule.
    # Residual spoofing risk is local-dev only: it needs raw sockets on
    # the bridge, and the bootstrap still requires DEBUG + an explicit
    # opt-in + a valid parent e-mail, never applies to /api/, and only
    # grants the configured parent's own view of the dashboard.
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


def _configured_parent():
    # The ONLY identity source: server-side settings, never anything the
    # request supplies. Fail closed unless the configured e-mail names an
    # existing, active parent account.
    email = getattr(settings, "LOCAL_UI_TEST_USER_EMAIL", "")
    if not email:
        return None
    user_model = get_user_model()
    return user_model.objects.filter(
        email=email,
        role=user_model.ROLE_PARENT,
        is_active=True,
    ).first()


def _bootstrap_path(path):
    if path in _BOOTSTRAP_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in _BOOTSTRAP_PREFIXES)


class LocalUiTestBypassMiddleware:
    """LOCAL UI TESTING ONLY (never production): establishes a NORMAL
    Django session for the configured local parent on local dashboard
    page loads so the panel and its owner-scoped API calls work without
    the login page.

    Fail-closed gates, all required:
      - every request to /api/ is skipped (Phase A F-05: the bypass can
        never grant API access by itself - APIs still demand a session);
      - only / and /dashboard/* paths bootstrap (login form, static
        assets and unknown paths are never touched);
      - an existing authenticated session is never replaced;
      - DEBUG + explicit LOCAL_UI_TEST_BYPASS opt-in + local client
        (loopback or the container's own bridge gateway) + a configured
        e-mail naming an existing active parent.
    Session cookie, CSRF middleware and CSRF tokens all stay standard.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/api/"):
            return self.get_response(request)
        if not _bootstrap_path(request.path):
            return self.get_response(request)
        if request.user.is_authenticated:
            return self.get_response(request)
        if not bypass_active(request):
            return self.get_response(request)
        user = _configured_parent()
        if user is None:
            return self.get_response(request)
        auth_login(request, user)
        # Marker for templates and api.js: this page's identity came
        # from the local dev session bootstrap, not a manual sign-in.
        request.local_ui_active = True
        return self.get_response(request)

import uuid
from pathlib import Path

import pytest
from accounts.models import Parent
from accounts.services import create_parent_account
from core import local_ui_bypass as bypass_mod
from devices.models import Robot
from django.test import override_settings
from rest_framework.test import APIClient

from .helpers import setup_parent

pytestmark = pytest.mark.django_db

BYPASS_EMAIL = "bypass-ui-parent@example.com"


@pytest.fixture
def bypass_parent():
    user = create_parent_account(BYPASS_EMAIL, "unused-local-ui-password")
    return Parent.objects.get(user=user)


@pytest.fixture
def other_parent(api_client):
    return setup_parent(api_client, "bypass-ui-other@example.com")


def _robot(parent, nickname=""):
    return Robot.objects.create(
        device_id=str(uuid.uuid4()),
        parent=parent,
        nickname=nickname,
    )


def _settings_on(**extra):
    return override_settings(
        DEBUG=True,
        LOCAL_UI_TEST_BYPASS=True,
        LOCAL_UI_TEST_USER_EMAIL=BYPASS_EMAIL,
        **extra,
    )


# --- Gates: any single condition false => normal authentication -----------


def test_debug_false_disables_bypass(bypass_parent):
    with override_settings(
        DEBUG=False,
        LOCAL_UI_TEST_BYPASS=True,
        LOCAL_UI_TEST_USER_EMAIL=BYPASS_EMAIL,
    ):
        response = APIClient().get("/dashboard/")
    assert response.status_code == 302
    assert response.url.startswith("/login/")


def test_bypass_env_false_disables_bypass(bypass_parent):
    with override_settings(
        DEBUG=True,
        LOCAL_UI_TEST_BYPASS=False,
        LOCAL_UI_TEST_USER_EMAIL=BYPASS_EMAIL,
    ):
        response = APIClient().get("/dashboard/")
    assert response.status_code == 302
    assert response.url.startswith("/login/")


def test_non_loopback_request_disables_bypass(bypass_parent):
    with _settings_on():
        response = APIClient().get("/dashboard/", REMOTE_ADDR="203.0.113.9")
    assert response.status_code == 302
    assert response.url.startswith("/login/")


def test_empty_email_disables_bypass(bypass_parent):
    with override_settings(
        DEBUG=True,
        LOCAL_UI_TEST_BYPASS=True,
        LOCAL_UI_TEST_USER_EMAIL="",
    ):
        response = APIClient().get("/dashboard/")
    assert response.status_code == 302


def test_unknown_email_disables_bypass(bypass_parent):
    with override_settings(
        DEBUG=True,
        LOCAL_UI_TEST_BYPASS=True,
        LOCAL_UI_TEST_USER_EMAIL="nobody@example.com",
    ):
        response = APIClient().get("/dashboard/")
    assert response.status_code == 302


def test_inactive_user_disables_bypass(bypass_parent):
    bypass_parent.user.is_active = False
    bypass_parent.user.save()
    with _settings_on():
        response = APIClient().get("/dashboard/")
    assert response.status_code == 302


# --- All gates true => local dashboard/controller work without login ------


def test_dashboard_accessible_without_login(bypass_parent):
    _robot(bypass_parent, nickname="BypassBotty")
    with _settings_on():
        client = APIClient()
        response = client.get("/dashboard/")
    assert response.status_code == 200
    html = response.content.decode()
    assert "BypassBotty" in html
    # Preferred design: the local bootstrap establishes a REAL session for
    # the parent named by settings, so the page's own owner-scoped API
    # calls work without a manual sign-in.
    assert client.session["_auth_user_id"] == str(bypass_parent.user.pk)


def test_controller_page_accessible_without_login(bypass_parent):
    robot = _robot(bypass_parent, nickname="BypassControllerBot")
    with _settings_on():
        response = APIClient().get(f"/dashboard/robots/{robot.pk}/")
    assert response.status_code == 200
    html = response.content.decode()
    assert f'data-robot-id="{robot.pk}"' in html
    assert "BypassControllerBot" in html
    for action in (
        "WAVE",
        "NOD",
        "TILT_HEAD",
        "POINT",
        "DANCE",
        "LISTENING_RETRY",
        "IDLE",
    ):
        assert f'data-action="{action}"' in html, action


def test_controller_page_apis_require_auth_even_under_bypass(bypass_parent):
    # Phase A (F-05): the local-UI bypass never authenticates /api/ routes.
    # The controller page's JS calls these owner-scoped APIs; under the
    # bypass they return 401 (the page itself still renders — see
    # test_controller_page_accessible_without_login).
    robot = _robot(bypass_parent)
    with _settings_on():
        client = APIClient()
        summary = client.get(f"/api/v1/devices/{robot.pk}/summary/")
        state = client.get(f"/api/v1/devices/{robot.pk}/state/")
    assert summary.status_code == 401
    assert state.status_code == 401


def test_command_post_under_bypass_is_401(bypass_parent):
    # Writes are gated the same way: the bypass can never drive a robot
    # through the REST command contract without a real session.
    robot = _robot(bypass_parent)
    with _settings_on():
        response = APIClient().post(
            f"/api/v1/devices/{robot.pk}/command/",
            {"action": "wave"},
            format="json",
        )
    assert response.status_code == 401


def test_login_page_still_renders_with_bypass_active(bypass_parent):
    with _settings_on():
        response = APIClient().get("/login/")
    assert response.status_code == 200
    assert 'name="csrfmiddlewaretoken"' in response.content.decode()


# --- Ownership rules are unchanged under the bypass -----------------------


def test_bypass_cannot_open_another_parents_controller(bypass_parent, other_parent):
    stranger_robot = _robot(other_parent, nickname="StrangersBotty")
    with _settings_on():
        response = APIClient().get(f"/dashboard/robots/{stranger_robot.pk}/")
    assert response.status_code == 404


def test_bypass_dashboard_only_lists_own_robots(bypass_parent, other_parent):
    _robot(bypass_parent, nickname="MyBotty")
    _robot(other_parent, nickname="StrangersBotty")
    with _settings_on():
        html = APIClient().get("/dashboard/").content.decode()
    assert "MyBotty" in html
    assert "StrangersBotty" not in html


def test_bypass_never_overrides_a_real_login(bypass_parent, other_parent, api_client):
    # Session belongs to other_parent; the bypass e-mail names a different
    # parent - the real session must win.
    _robot(other_parent, nickname="RealSessionBot")
    _robot(bypass_parent, nickname="BypassBot")
    with _settings_on():
        html = api_client.get("/dashboard/").content.decode()
    assert "RealSessionBot" in html
    assert "BypassBot" not in html


# --- API authentication is unaffected when the bypass is inactive ---------


def test_api_still_requires_auth_when_bypass_inactive(bypass_parent):
    robot = _robot(bypass_parent)
    with override_settings(
        DEBUG=False,
        LOCAL_UI_TEST_BYPASS=True,
        LOCAL_UI_TEST_USER_EMAIL=BYPASS_EMAIL,
    ):
        response = APIClient().get(f"/api/v1/devices/{robot.pk}/state/")
    assert response.status_code == 401


def test_api_still_requires_auth_when_flag_off(bypass_parent):
    robot = _robot(bypass_parent)
    with override_settings(
        DEBUG=True,
        LOCAL_UI_TEST_BYPASS=False,
        LOCAL_UI_TEST_USER_EMAIL=BYPASS_EMAIL,
    ):
        response = APIClient().get(f"/api/v1/devices/{robot.pk}/state/")
    assert response.status_code == 401


def test_default_settings_have_bypass_disabled():
    from django.conf import settings

    assert settings.LOCAL_UI_TEST_BYPASS is False
    assert settings.LOCAL_UI_TEST_USER_EMAIL == ""


# --- Docker-published-port clients (bridge gateway, not loopback) ---------


def test_docker_gateway_client_is_treated_as_local(bypass_parent, monkeypatch):
    monkeypatch.setattr(bypass_mod, "_default_gateway", lambda: "172.20.0.1")
    _robot(bypass_parent, nickname="GatewayBotty")
    with _settings_on():
        response = APIClient().get("/dashboard/", REMOTE_ADDR="172.20.0.1")
    assert response.status_code == 200
    assert "GatewayBotty" in response.content.decode()


def test_non_gateway_client_still_requires_loopback(bypass_parent, monkeypatch):
    monkeypatch.setattr(bypass_mod, "_default_gateway", lambda: "172.20.0.1")
    with _settings_on():
        response = APIClient().get("/dashboard/", REMOTE_ADDR="172.20.0.99")
    assert response.status_code == 302
    assert response.url.startswith("/login/")


def test_gateway_client_cannot_use_api_even_under_bypass(bypass_parent, monkeypatch):
    # Even the gateway-accepted local client is refused on /api/ - the
    # bypass stays view-only for server-rendered pages (Phase A F-05).
    robot = _robot(bypass_parent)
    monkeypatch.setattr(bypass_mod, "_default_gateway", lambda: "172.20.0.1")
    with _settings_on():
        response = APIClient().get(
            f"/api/v1/devices/{robot.pk}/state/", REMOTE_ADDR="172.20.0.1"
        )
    assert response.status_code == 401


# --- Frontend marker: the login redirect is gated, not removed ------------


def test_bypass_marker_meta_renders_for_bypass_identity(bypass_parent):
    _robot(bypass_parent, nickname="MetaBotty")
    with _settings_on():
        html = APIClient().get("/dashboard/").content.decode()
    assert '<meta name="local-ui-bypass" content="1">' in html
    assert "MetaBotty" in html


def test_bypass_marker_meta_absent_without_opt_in(bypass_parent):
    with override_settings(
        DEBUG=True,
        LOCAL_UI_TEST_BYPASS=False,
        LOCAL_UI_TEST_USER_EMAIL=BYPASS_EMAIL,
    ):
        html = APIClient().get("/login/").content.decode()
    assert 'name="local-ui-bypass"' not in html
    with override_settings(
        DEBUG=False,
        LOCAL_UI_TEST_BYPASS=True,
        LOCAL_UI_TEST_USER_EMAIL=BYPASS_EMAIL,
    ):
        html = APIClient().get("/login/").content.decode()
    assert 'name="local-ui-bypass"' not in html


def test_bypass_marker_meta_absent_for_real_session(bypass_parent, api_client):
    # A real login session never gets the bypass marker, so api.js keeps
    # its normal session-expiry redirect for it.
    setup_parent(api_client, "meta-real-session@example.com")
    with _settings_on():
        response = api_client.get("/dashboard/")
    assert response.status_code == 200
    assert 'name="local-ui-bypass"' not in response.content.decode()


# --- Local wiring: the opt-in must reach the container --------------------


def _repo_root():
    return Path(__file__).resolve().parents[3]


def test_compose_forwards_bypass_flags_with_default_off():
    compose_path = _repo_root() / "infra" / "docker-compose.yml"
    compose = compose_path.read_text(encoding="utf-8")
    assert "LOCAL_UI_TEST_BYPASS: ${LOCAL_UI_TEST_BYPASS:-false}" in compose
    assert "LOCAL_UI_TEST_USER_EMAIL: ${LOCAL_UI_TEST_USER_EMAIL:-}" in compose


def test_env_example_documents_bypass_keys():
    example = (_repo_root() / ".env.example").read_text(encoding="utf-8")
    assert "LOCAL_UI_TEST_BYPASS=false" in example
    assert "LOCAL_UI_TEST_USER_EMAIL=" in example


def test_api_js_login_redirect_gated_by_bypass_marker():
    api_js = (_repo_root() / "backend" / "static" / "js" / "api.js").read_text(
        encoding="utf-8"
    )
    assert 'meta[name="local-ui-bypass"]' in api_js
    assert "BYPASS_META" in api_js
    # Exactly one auto-redirect exists and it sits inside the gate.
    assert api_js.count('window.location.assign("/login/?next=" + next)') == 1


# --- Sign-in-free local session: page loads bootstrap a real session -------
# Preferred design (Phase 2): with every fail-closed gate true, a local
# dashboard page load establishes a NORMAL Django session for the parent
# named by LOCAL_UI_TEST_USER_EMAIL, so the controller's owner-scoped API
# reads and semantic command writes work live. /api/ itself never
# bootstraps; identity comes only from server settings.


@pytest.fixture
def publish_recorder(monkeypatch):
    from devices import views

    calls = []

    def _record(robot, payload):
        calls.append((robot, payload))

    monkeypatch.setattr(views, "publish_command", _record)
    return calls


@pytest.fixture
def clear_cooldowns():
    from devices.views import _command_cooldowns

    _command_cooldowns.clear()
    yield
    _command_cooldowns.clear()


def test_bootstrap_establishes_configured_parent_session(bypass_parent):
    from django.conf import settings

    _robot(bypass_parent, nickname="SessionBotty")
    with _settings_on():
        client = APIClient()
        response = client.get("/dashboard/")
    assert response.status_code == 200
    assert "SessionBotty" in response.content.decode()
    assert settings.SESSION_COOKIE_NAME in client.cookies
    assert client.session["_auth_user_id"] == str(bypass_parent.user.pk)


def test_root_path_bootstraps_session(bypass_parent):
    from django.conf import settings

    with _settings_on():
        client = APIClient()
        response = client.get("/")
    assert response.status_code == 302
    assert settings.SESSION_COOKIE_NAME in client.cookies
    assert client.session["_auth_user_id"] == str(bypass_parent.user.pk)


def test_bootstrap_session_reads_owner_scoped_controller_endpoints(bypass_parent):
    robot = _robot(bypass_parent, nickname="LiveBot")
    with _settings_on():
        client = APIClient()
        assert client.get("/dashboard/").status_code == 200
        summary = client.get(f"/api/v1/devices/{robot.pk}/summary/")
        state = client.get(f"/api/v1/devices/{robot.pk}/state/")
        events = client.get(f"/api/v1/devices/{robot.pk}/events/")
        sensors = client.get(f"/api/v1/devices/{robot.pk}/sensors/")
        configuration = client.get(f"/api/v1/devices/{robot.pk}/configuration/")
    assert summary.status_code == 200
    assert summary.data["robot"]["id"] == str(robot.pk)
    assert state.status_code == 200
    assert events.status_code == 200
    assert isinstance(events.data, list)
    assert sensors.status_code == 200
    assert isinstance(sensors.data, list)
    assert configuration.status_code == 200


def test_bootstrapped_session_cross_owner_endpoints_are_404(
    bypass_parent, other_parent
):
    stranger_robot = _robot(other_parent, nickname="StrangersBotty")
    with _settings_on():
        client = APIClient()
        assert client.get("/dashboard/").status_code == 200
        state = client.get(f"/api/v1/devices/{stranger_robot.pk}/state/")
        summary = client.get(f"/api/v1/devices/{stranger_robot.pk}/summary/")
        page = client.get(f"/dashboard/robots/{stranger_robot.pk}/")
    assert state.status_code == 404
    assert summary.status_code == 404
    assert page.status_code == 404


def test_bootstrapped_session_sends_semantic_command(
    bypass_parent, publish_recorder, clear_cooldowns
):
    robot = _robot(bypass_parent, nickname="CmdBot")
    with _settings_on():
        client = APIClient()
        assert client.get("/dashboard/").status_code == 200
        response = client.post(
            f"/api/v1/devices/{robot.pk}/command/",
            {"action": "wave"},
            format="json",
        )
    assert response.status_code == 200, response.content
    assert len(publish_recorder) == 1
    recorded_robot, payload = publish_recorder[0]
    assert recorded_robot.pk == robot.pk
    # Contract: case-insensitive membership, verbatim pass-through.
    assert payload["action"] == "wave"


def test_bootstrapped_session_still_enforces_csrf(
    bypass_parent, publish_recorder, clear_cooldowns
):
    from django.conf import settings

    robot = _robot(bypass_parent, nickname="CsrfBot")
    with _settings_on():
        client = APIClient(enforce_csrf_checks=True)
        assert client.get("/dashboard/").status_code == 200
        token = client.cookies[settings.CSRF_COOKIE_NAME].value
        missing = client.post(
            f"/api/v1/devices/{robot.pk}/command/",
            {"action": "wave"},
            format="json",
        )
        assert missing.status_code == 403
        assert publish_recorder == []
        client.credentials(HTTP_X_CSRFTOKEN=token)
        ok = client.post(
            f"/api/v1/devices/{robot.pk}/command/",
            {"action": "wave"},
            format="json",
        )
    assert ok.status_code == 200, ok.content
    assert len(publish_recorder) == 1


def test_bootstrapped_session_rejects_invalid_action(
    bypass_parent, publish_recorder, clear_cooldowns
):
    robot = _robot(bypass_parent, nickname="BadCmdBot")
    with _settings_on():
        client = APIClient()
        assert client.get("/dashboard/").status_code == 200
        response = client.post(
            f"/api/v1/devices/{robot.pk}/command/",
            {"action": "servo"},
            format="json",
        )
    assert response.status_code == 400
    assert publish_recorder == []


def test_bootstrapped_session_command_cooldown_still_applies(
    bypass_parent, publish_recorder, clear_cooldowns
):
    robot = _robot(bypass_parent, nickname="CoolBot")
    with _settings_on():
        client = APIClient()
        assert client.get("/dashboard/").status_code == 200
        first = client.post(
            f"/api/v1/devices/{robot.pk}/command/",
            {"action": "wave"},
            format="json",
        )
        second = client.post(
            f"/api/v1/devices/{robot.pk}/command/",
            {"action": "wave"},
            format="json",
        )
    assert first.status_code == 200, first.content
    assert second.status_code == 429
    assert len(publish_recorder) == 1


def test_api_paths_never_bootstrap_a_session(bypass_parent, monkeypatch):
    from django.conf import settings

    monkeypatch.setattr(bypass_mod, "_default_gateway", lambda: "172.20.0.1")
    robot = _robot(bypass_parent)
    with _settings_on():
        client = APIClient()
        response = client.get(
            f"/api/v1/devices/{robot.pk}/state/", REMOTE_ADDR="172.20.0.1"
        )
    assert response.status_code == 401
    assert settings.SESSION_COOKIE_NAME not in client.cookies
    assert "_auth_user_id" not in client.session


def test_login_page_never_bootstraps_a_session(bypass_parent):
    from django.conf import settings

    with _settings_on():
        client = APIClient()
        response = client.get("/login/")
    assert response.status_code == 200
    assert settings.SESSION_COOKIE_NAME not in client.cookies
    assert "_auth_user_id" not in client.session


def test_unknown_path_never_bootstraps_a_session(bypass_parent):
    from django.conf import settings

    with _settings_on():
        client = APIClient()
        response = client.get("/no-such-page/")
    assert response.status_code == 404
    assert settings.SESSION_COOKIE_NAME not in client.cookies
    assert "_auth_user_id" not in client.session


def test_missing_or_invalid_parent_never_creates_a_session(bypass_parent):
    from django.conf import settings

    with override_settings(
        DEBUG=True,
        LOCAL_UI_TEST_BYPASS=True,
        LOCAL_UI_TEST_USER_EMAIL="",
    ):
        client = APIClient()
        assert client.get("/dashboard/").status_code == 302
        assert settings.SESSION_COOKIE_NAME not in client.cookies

    with override_settings(
        DEBUG=True,
        LOCAL_UI_TEST_BYPASS=True,
        LOCAL_UI_TEST_USER_EMAIL="nobody@example.com",
    ):
        client = APIClient()
        assert client.get("/dashboard/").status_code == 302
        assert settings.SESSION_COOKIE_NAME not in client.cookies

    bypass_parent.user.is_active = False
    bypass_parent.user.save()
    with _settings_on():
        client = APIClient()
        assert client.get("/dashboard/").status_code == 302
        assert settings.SESSION_COOKIE_NAME not in client.cookies


def test_disabled_config_never_creates_a_session(bypass_parent):
    from django.conf import settings

    for flags in (
        dict(
            DEBUG=True,
            LOCAL_UI_TEST_BYPASS=False,
            LOCAL_UI_TEST_USER_EMAIL=BYPASS_EMAIL,
        ),
        dict(
            DEBUG=False,
            LOCAL_UI_TEST_BYPASS=True,
            LOCAL_UI_TEST_USER_EMAIL=BYPASS_EMAIL,
        ),
    ):
        with override_settings(**flags):
            client = APIClient()
            response = client.get("/dashboard/")
        assert response.status_code == 302
        assert settings.SESSION_COOKIE_NAME not in client.cookies


def test_non_local_client_never_creates_a_session(bypass_parent, monkeypatch):
    from django.conf import settings

    monkeypatch.setattr(bypass_mod, "_default_gateway", lambda: "172.20.0.1")
    for remote_addr in ("203.0.113.9", "172.20.0.99"):
        with _settings_on():
            client = APIClient()
            response = client.get("/dashboard/", REMOTE_ADDR=remote_addr)
        assert response.status_code == 302
        assert settings.SESSION_COOKIE_NAME not in client.cookies


def test_bootstrap_session_key_is_stable_across_requests(bypass_parent):
    with _settings_on():
        client = APIClient()
        assert client.get("/dashboard/").status_code == 200
        first_key = client.session.session_key
        assert client.get("/dashboard/").status_code == 200
        second_key = client.session.session_key
    assert first_key
    assert first_key == second_key

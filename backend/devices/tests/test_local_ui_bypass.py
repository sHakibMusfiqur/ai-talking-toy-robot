import uuid

import pytest
from accounts.models import Parent
from accounts.services import create_parent_account
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
    # The bypass never creates a login session.
    assert "_auth_user_id" not in client.session


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

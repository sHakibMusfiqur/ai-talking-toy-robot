import re
import uuid
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path

import pytest
from configuration.models import Configuration
from devices.models import Robot, RobotState
from django.utils import timezone
from rest_framework.test import APIClient

from .helpers import setup_admin, setup_parent

pytestmark = pytest.mark.django_db

FORBIDDEN_HARDWARE_TERMS = ("servo", "pwm", "gpio", "motor", "voltage")


class _VisibleTextExtractor(HTMLParser):
    """Extract user-visible text nodes from rendered HTML.

    Attribute values are never inspected — they carry dynamic identifiers,
    CSRF tokens and URLs, which are data, not UI copy. <head>, <script>
    and <style> content is skipped for the same reason (the authored copy
    itself is covered by the raw-template scan in the terminology test).
    """

    _NON_VISIBLE = {"head", "script", "style"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._chunks = []

    def handle_starttag(self, tag, attrs):
        if tag in self._NON_VISIBLE:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._NON_VISIBLE and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth:
            self._chunks.append(data)

    def text(self):
        return " ".join(self._chunks).lower()


def _visible_text(html):
    extractor = _VisibleTextExtractor()
    extractor.feed(html)
    return extractor.text()


def _robot(parent, nickname="", last_seen=None, firmware_version="1.2.3"):
    return Robot.objects.create(
        device_id=str(uuid.uuid4()),
        parent=parent,
        nickname=nickname,
        firmware_version=firmware_version,
        last_seen=last_seen,
    )


def controller_url(robot):
    return f"/dashboard/robots/{robot.pk}/"


def stat_value(html, name):
    match = re.search(
        rf'data-stat="{re.escape(name)}"\s+data-value="([^"]*)"', html
    )
    assert match, f"missing data-stat={name} on dashboard"
    return match.group(1)


@pytest.fixture
def parent(api_client):
    return setup_parent(api_client, "ui-parent@example.com")


@pytest.fixture
def stranger():
    client = APIClient()
    setup_parent(client, "ui-stranger@example.com")
    return client


@pytest.fixture
def admin(api_client):
    return setup_admin(api_client, "ui-admin@example.com")


@pytest.fixture
def recorder(monkeypatch):
    """Capture publishes at the view binding — broker-independent."""
    from devices import views

    calls = []

    def _record(robot, payload):
        calls.append((robot, payload))

    monkeypatch.setattr(views, "publish_command", _record)
    return calls


# --- Dashboard: access control -------------------------------------------


def test_dashboard_requires_login(api_client):
    response = api_client.get("/dashboard/")
    assert response.status_code == 302
    assert response.url.startswith("/login/")


def test_controller_page_requires_login(api_client):
    response = api_client.get("/dashboard/robots/" + "a" * 24 + "/")
    assert response.status_code == 302
    assert response.url.startswith("/login/?next=")


def test_dashboard_renders_own_robots(api_client, parent):
    robot = _robot(parent, nickname="Botty")
    response = api_client.get("/dashboard/")
    assert response.status_code == 200
    html = response.content.decode()
    assert "Botty" in html
    assert robot.device_id in html
    assert str(robot.pk) in html


def test_dashboard_shows_only_own_robots(api_client, parent, stranger):
    stranger_parent = _robot_parent_of(stranger)
    other_robot = _robot(stranger_parent, nickname="StrangersBotty")
    own_robot = _robot(parent, nickname="MyBotty")
    response = api_client.get("/dashboard/")
    html = response.content.decode()
    assert own_robot.device_id in html
    assert other_robot.device_id not in html
    assert "StrangersBotty" not in html


def _robot_parent_of(client):
    from accounts.models import Parent

    user_id = client.session["_auth_user_id"]
    return Parent.objects.get(user__id=user_id)


def test_dashboard_counts_only_own_robots(api_client, parent, stranger):
    own_a = _robot(parent, nickname="OnlineBot")
    _robot(parent, nickname="OfflineBot")
    RobotState.objects.create(robot=own_a, online=True, state="idle")
    # Another parent's online robot must not leak into this parent's counts.
    other_robot = _robot(_robot_parent_of(stranger))
    RobotState.objects.create(robot=other_robot, online=True, state="idle")

    response = api_client.get("/dashboard/")
    html = response.content.decode()
    assert stat_value(html, "total") == "2"
    assert stat_value(html, "online") == "1"
    assert stat_value(html, "offline") == "1"


def test_recently_active_is_ownership_safe(api_client, parent, stranger):
    own_robot = _robot(
        parent, nickname="MyLateBot", last_seen=timezone.now() - timedelta(hours=1)
    )
    other_robot = _robot(
        _robot_parent_of(stranger), nickname="OtherFresh", last_seen=timezone.now()
    )
    response = api_client.get("/dashboard/")
    html = response.content.decode()
    assert stat_value(html, "recently-active") == str(own_robot.pk)
    assert "OtherFresh" not in html


def test_dashboard_empty_state(api_client, parent):
    response = api_client.get("/dashboard/")
    html = response.content.decode()
    assert response.status_code == 200
    assert "No robots paired yet" in html
    assert stat_value(html, "total") == "0"


def test_admin_dashboard_is_not_a_fleet_view(api_client, admin, stranger):
    other_robot = _robot(_robot_parent_of(stranger), nickname="OtherBot")
    response = api_client.get("/dashboard/")
    assert response.status_code == 200
    html = response.content.decode()
    assert other_robot.device_id not in html
    assert "OtherBot" not in html
    assert "No robots paired yet" in html


# --- Controller page: ownership boundary ---------------------------------


def test_controller_page_renders_for_owner(api_client, parent):
    robot = _robot(parent, nickname="ControllerBot")
    response = api_client.get(controller_url(robot))
    assert response.status_code == 200
    html = response.content.decode()
    assert f'data-robot-id="{robot.pk}"' in html
    assert "ControllerBot" in html
    assert robot.device_id in html


def test_controller_page_404_for_other_parents(api_client, parent, stranger):
    other_robot = _robot(_robot_parent_of(stranger))
    response = api_client.get(controller_url(other_robot))
    assert response.status_code == 404


def test_controller_page_404_for_unknown_robot(api_client, parent):
    response = api_client.get("/dashboard/robots/" + "f" * 24 + "/")
    assert response.status_code == 404


def test_controller_page_404_for_malformed_id(api_client, parent):
    response = api_client.get("/dashboard/robots/not-an-objectid/")
    assert response.status_code == 404


def test_controller_page_404_has_no_traceback(api_client, parent):
    response = api_client.get("/dashboard/robots/not-an-objectid/")
    html = response.content.decode()
    assert response.status_code == 404
    assert "Traceback" not in html
    assert "Exception Type" not in html
    assert "Using the URLconf defined in" not in html


# --- API boundary pins ----------------------------------------------------


def test_parent_device_list_still_admin_only(api_client, parent):
    response = api_client.get("/api/v1/devices/")
    assert response.status_code == 403


# --- Controller page: semantic command surface ----------------------------

SEMANTIC_LABELS = [
    "Idle",
    "Wave",
    "Dance",
    "Nod",
    "Tilt Head",
    "Point",
    "Listening Retry",
]

SEMANTIC_ACTIONS = [
    "IDLE",
    "WAVE",
    "DANCE",
    "NOD",
    "TILT_HEAD",
    "POINT",
    "LISTENING_RETRY",
]


def test_controller_page_exposes_only_semantic_actions(api_client, parent):
    robot = _robot(parent)
    html = api_client.get(controller_url(robot)).content.decode()
    for label in SEMANTIC_LABELS:
        assert f">{label}</button>" in html, label
    for action in SEMANTIC_ACTIONS:
        assert f'data-action="{action}"' in html, action


def test_controller_page_has_no_hardware_terminology(api_client, parent):
    robot = _robot(parent)
    html = api_client.get(controller_url(robot)).content.decode()
    text = _visible_text(html)
    
    assert "robot actions" in text
   
    for dynamic_value in (robot.device_id, str(robot.pk), parent.user.email):
        text = text.replace(dynamic_value.lower(), "")
    for term in FORBIDDEN_HARDWARE_TERMS:
        assert term not in text, term
  
    templates_dir = Path(__file__).resolve().parents[2] / "templates"
    for template in (
        "base.html",
        "dashboard/controller.html",
        "dashboard/partials/sidebar.html",
    ):
        source = (templates_dir / template).read_text(encoding="utf-8").lower()
        for term in FORBIDDEN_HARDWARE_TERMS:
            assert term not in source, f"{term} found in {template}"


def test_hardware_terminology_scan_checks_visible_copy_not_attributes():
    
    assert "pwm" not in _visible_text('<div data-x="pwm" title="GPIO">Status</div>')
   
    visible = _visible_text("<div>Servo calibration</div>")
    assert "servo" in visible


def test_controller_page_is_read_only_no_forms(api_client, parent):
    robot = _robot(parent)
    html = api_client.get(controller_url(robot)).content.decode().lower()
    assert "<form" not in html


def test_controller_page_contains_no_config_secrets(api_client, parent):
    robot = _robot(parent)
    Configuration.objects.create(
        robot=robot,
        version=1,
        data={"wifi_password": "hunter2-secret", "volume": 5},
        is_current=True,
    )
    html = api_client.get(controller_url(robot)).content.decode()
    assert "hunter2-secret" not in html


# --- Login handoff --------------------------------------------------------


def test_login_next_defaults_to_dashboard(api_client):
    html = api_client.get("/login/").content.decode()
    assert 'name="next"' in html
    assert 'value="/dashboard/"' in html


# --- CSRF -----------------------------------------------------------------

def test_command_from_browser_requires_csrf_token(recorder):
    client = APIClient(enforce_csrf_checks=True)
    page = client.get("/login/")
    assert page.status_code == 200
    client.credentials(
        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value
    )
    parent = setup_parent(client, "ui-csrf@example.com")
    robot = _robot(parent)
    # The login exchange rotates the CSRF cookie — the browser always
    # sends the *current* cookie value with X-CSRFToken, so do the same.
    client.credentials(HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)

    # Valid token: the command goes through.
    ok = client.post(
        f"/api/v1/devices/{robot.pk}/command/", {"action": "wave"}, format="json"
    )
    assert ok.status_code == 200, ok.content
    assert len(recorder) == 1

    # Token removed: same session, but CSRF is enforced (403, not 401).
    client.credentials()
    bad = client.post(
        f"/api/v1/devices/{robot.pk}/command/", {"action": "wave"}, format="json"
    )
    assert bad.status_code == 403
    assert len(recorder) == 1


# --- Premium UI / accessibility / build-source contracts -------------------


def test_dashboard_mobile_nav_accessibility(api_client, parent):
    _robot(parent)
    html = api_client.get("/dashboard/").content.decode()
    assert 'id="sidebar"' in html
    assert "data-nav-toggle" in html
    assert 'aria-expanded="false"' in html
    assert 'aria-controls="sidebar"' in html
    assert 'aria-label="Main navigation"' in html


def test_controller_sections_expose_state_elements(api_client, parent):
    robot = _robot(parent)
    html = api_client.get(controller_url(robot)).content.decode()
    for element_id in (
        "state-loading",
        "state-values",
        "state-error",
        "state-retry",
        "events-loading",
        "events-list",
        "events-empty",
        "events-error",
        "events-retry",
        "sensors-loading",
        "sensors-list",
        "sensors-empty",
        "sensors-error",
        "sensors-retry",
        "config-loading",
        "config-values",
        "config-error",
        "config-retry",
    ):
        assert f'id="{element_id}"' in html, element_id
    assert 'aria-live="polite"' in html
    assert 'role="group" aria-label="Robot commands"' in html


def test_pages_link_generated_stylesheet(api_client, parent):
    _robot(parent)
    for url in ("/login/", "/dashboard/"):
        html = api_client.get(url).content.decode()
        assert "css/app.css" in html, url


def test_tailwind_source_uses_v3_directives_and_reduced_motion():
    source_path = (
        Path(__file__).resolve().parents[3] / "frontend" / "styles" / "input.css"
    )
    source = source_path.read_text(encoding="utf-8")
    assert "@tailwind base;" in source
    assert "@tailwind components;" in source
    assert "@tailwind utilities;" in source
    assert "prefers-reduced-motion" in source


def test_no_frontend_framework_or_mqtt_in_templates():
    templates_root = Path(__file__).resolve().parents[3] / "backend" / "templates"
    forbidden = ("react", "vue", "svelte", "angular", "mqtt://", "wss://", "vite")
    for path in templates_root.rglob("*.html"):
        content = path.read_text(encoding="utf-8").lower()
        for term in forbidden:
            assert term not in content, f"{term} found in {path.name}"


# --- UI-only phase: mock service layer + disabled future sections ----------


def test_controller_page_loads_mock_service_layer(api_client, parent):
    robot = _robot(parent, nickname="MockLayerBot")
    html = api_client.get(controller_url(robot)).content.decode()
    for asset in (
        "js/mocks/robotMock.js",
        "js/services/robotMockService.js",
        "js/services/robotCommandService.js",
        "js/ui/toast.js",
    ):
        assert asset in html, asset
    assert "img/robot-placeholder.svg" in html


def test_controller_page_uses_local_placeholder_only(api_client, parent):
    robot = _robot(parent)
    html = api_client.get(controller_url(robot)).content.decode()
    assert 'src="http' not in html
    assert 'href="http' not in html
    assert 'src="/static/img/robot-placeholder.svg"' in html


def test_sidebar_future_sections_are_disabled_not_linked(api_client, parent):
    _robot(parent)
    html = api_client.get("/dashboard/").content.decode()
    assert html.count('aria-disabled="true"') >= 3
    for label in ("Conversations", "Events", "Settings", "Soon"):
        assert label in html, label
    for section in ("conversations", "events", "settings"):
        assert not re.search(rf'href="[^"]*{section}', html, re.I), section


def test_sidebar_controller_link_targets_controller_page(api_client, parent):
    robot = _robot(parent, nickname="NavBot")
    html = api_client.get(controller_url(robot)).content.decode()
    assert f'href="/dashboard/robots/{robot.pk}/"' in html
    assert "Robot Controller" in html

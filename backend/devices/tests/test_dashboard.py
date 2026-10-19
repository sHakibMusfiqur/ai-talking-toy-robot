import re
import uuid
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.conf import settings
from django.test import override_settings

from configuration.models import Configuration
from devices.models import Robot, RobotState
from devices.services import PAIRING_CODE_ALPHABET
from django.utils import timezone
from rest_framework.test import APIClient

from .helpers import setup_admin, setup_parent

pytestmark = pytest.mark.django_db

FORBIDDEN_HARDWARE_TERMS = ("servo", "pwm", "gpio", "motor", "voltage")


class _VisibleTextExtractor(HTMLParser):


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
    assert 'id="robot-face"' in html


def test_controller_page_loads_no_external_or_static_robot_image(api_client, parent):
    robot = _robot(parent)
    html = api_client.get(controller_url(robot)).content.decode()
    assert 'src="http' not in html
    assert 'href="http' not in html
    # The preview is the real 3D face container - never a static robot image.
    assert "<img" not in html
    assert "robot-placeholder" not in html
    assert 'id="robot-face"' in html


# --- Interactive 3D robot face --------------------------------------------


def test_controller_page_renders_3d_face_with_loading_and_fallback(api_client, parent):
    robot = _robot(parent)
    html = api_client.get(controller_url(robot)).content.decode()
    assert 'id="robot-face"' in html
    assert 'data-face-state="loading"' in html
    assert 'id="face-loading"' in html
    assert 'id="face-fallback"' in html
    assert 'id="face-fallback-text"' in html
    assert 'aria-label="Robot face preview"' in html
    assert 'aria-live="polite"' in html
    assert "<img" not in html


def test_controller_page_loads_three_via_local_importmap(api_client, parent):
    robot = _robot(parent)
    html = api_client.get(controller_url(robot)).content.decode()
    assert '<script type="importmap">' in html
    assert '"three": "/static/vendor/three/three.module.js?v=' in html
    assert "https://" not in html
    assert 'type="module"' in html
    assert "js/robotFace.js" in html
    # The import map must be declared before the module that uses it.
    assert html.index('<script type="importmap">') < html.index('type="module"')


def test_controller_scripts_carry_asset_version(api_client, parent):
    """Every controller script (incl. the new face module) must be cache-busted
    - controller.js edits would otherwise serve stale from browser cache."""
    robot = _robot(parent)
    html = api_client.get(controller_url(robot)).content.decode()
    for asset in (
        "js/dashboard.js",
        "js/api.js",
        "js/mocks/robotMock.js",
        "js/services/robotMockService.js",
        "js/services/robotCommandService.js",
        "js/ui/toast.js",
        "js/controller.js",
        "js/robotFace.js",
    ):
        assert f'src="/static/{asset}?v={settings.ASSET_VERSION}"' in html, asset


def test_three_vendor_files_are_shipped():
    vendor = Path(__file__).resolve().parents[2] / "static" / "vendor" / "three"
    for name in ("three.module.js", "three.core.js"):
        assert (vendor / name).is_file(), name
    module = (vendor / "three.module.js").read_text(encoding="utf-8")
    assert "./three.core.js" in module


def test_robot_face_module_maps_confirmed_states_and_documents_provenance():
    js_dir = Path(__file__).resolve().parents[2] / "static" / "js"
    source = (js_dir / "robotFace.js").read_text(encoding="utf-8")
    # Real-telemetry state names the approved mapping keys off.
    for token in (
        '"CAPTURING"',
        '"PROCESSING"',
        '"LISTENING_RETRY"',
        '"DANCE"',
        '"WAVE"',
        "LOW_BATTERY",
        "robotface:state",
        "robotface:command",
        "prefers-reduced-motion",
        "hasWebGL",
        "3D preview unavailable",
        "REAL DEVICE TELEMETRY",
        "INFERRED",
    ):
        assert token in source, token
    # The face module must never fall back to a static image asset.
    for forbidden in (".png", ".gif", ".svg", "robot-placeholder"):
        assert forbidden not in source, forbidden


def test_robot_face_reduced_motion_events_update_expression():
    """F3 regression: robotface:* document listeners call the outer `wake`
    binding. start() must ASSIGN that binding - a nested wake declaration
    would shadow the stub and reduced-motion users would never see
    event-driven mood updates (no continuous loop to fall back on)."""
    js_dir = Path(__file__).resolve().parents[2] / "static" / "js"
    source = (js_dir / "robotFace.js").read_text(encoding="utf-8")
    # Outer no-op stub exists so listeners registered before three.js loads
    # never throw.
    assert "var wake = function () {};" in source
    # The nested declaration bug: `function wake(...)` anywhere redeclares a
    # scope-local binding and leaves listeners pointing at the stub.
    assert not re.search(r"\bfunction\s+wake\s*\(", source)
    # Real implementation is assigned to the shared binding inside start().
    assign = "wake = function () {"
    assert assign in source
    assign_body = source[source.index(assign) :]
    assert "if (reduced) frame(" in assign_body
    # Both face events reach that binding.
    state_listener = source[source.index('addEventListener("robotface:state"') :]
    assert "wake();" in state_listener[: state_listener.index("});")]
    command_listener = source[source.index('addEventListener("robotface:command"') :]
    assert "wake();" in command_listener[: command_listener.index("});")]


# --- RoboPanel face: geometry, expressions, gestures, color palette --------

CONTROLLER_TEMPLATE = (
    Path(__file__).resolve().parents[2] / "templates" / "dashboard" / "controller.html"
)


def _face_source():
    js_dir = Path(__file__).resolve().parents[2] / "static" / "js"
    return (js_dir / "robotFace.js").read_text(encoding="utf-8")


def test_robot_face_builds_reference_geometry_procedurally():
    source = _face_source()
    for token in (
        "ExtrudeGeometry",
        "roundedRect",
        "InstancedMesh",
        "TorusGeometry",
        "CylinderGeometry",
        "SphereGeometry",
        "PlaneGeometry",
        "CanvasTexture",
    ):
        assert token in source, token
    # The face builds geometry at runtime - it must never load image/model
    # assets (the preview is procedural, per the approved design reference).
    for forbidden in ("TextureLoader", "GLTFLoader", "OBJLoader", "ImageLoader"):
        assert forbidden not in source, forbidden
    # Recessed display (bezel opening) + pixel grid are real geometry.
    assert "holes.push" in source
    assert "FACE_CELLS" in source


def test_robot_face_expression_engine_covers_all_moods_with_fallback():
    source = _face_source()
    block = source[source.index("var EXPRESSIONS") : source.index("function expressionFor")]
    for mood in (
        "IDLE",
        "HAPPY",
        "SAD",
        "THINKING",
        "LISTENING",
        "EXCITED",
        "CONFUSED",
        "PLAYING",
        "OFFLINE",
        "BLINK",
    ):
        assert f"{mood}: makeExpression(" in block, mood
    # Unsupported moods degrade to IDLE instead of breaking the face.
    assert "EXPRESSIONS[mood] || EXPRESSIONS.IDLE" in source
    # Pixel patterns are ASCII rows (lit marker X, dark marker .).
    assert '"XXXXXX"' in source
    assert '"..........."' in source


def test_robot_face_pointer_gestures_drive_head_and_tap_reaction():
    source = _face_source()
    for token in (
        "pointerdown",
        "pointermove",
        "pointerup",
        "pointercancel",
        "setPointerCapture",
        "MAX_YAW",
        "MAX_PITCH",
        "TAP_MOVE_LIMIT",
        "TAP_TIME_LIMIT",
        'transient.mood = "EXCITED"',
        "1500",
        "onPointerLeave",
        'removeEventListener("pointerdown"',
    ):
        assert token in source, token
    # Dragging must not scroll the page underneath the preview.
    template = CONTROLLER_TEMPLATE.read_text(encoding="utf-8")
    face_markup = template[
        template.index('id="robot-face"') : template.index('id="face-loading"')
    ]
    assert "touch-none" in face_markup


def test_face_color_palette_markup_is_rendered(api_client, parent):
    robot = _robot(parent)
    html = api_client.get(controller_url(robot)).content.decode()
    assert 'id="face-palette"' in html
    for key in ("casing", "display", "eyes", "mouth", "glow"):
        assert f'data-palette="{key}"' in html, key
        assert f'data-palette-hex="{key}"' in html, key
        assert f'id="face-color-{key}"' in html, key
        assert f'id="face-hex-{key}"' in html, key
    for preset in ("amber", "blue", "pink", "green", "purple", "mono"):
        assert f'data-face-preset="{preset}"' in html, preset
    assert 'id="face-palette-reset"' in html
    assert "Reset to Default" in html
    # The palette sits inside the preview figure, below the 3D canvas.
    assert html.index('id="robot-face"') < html.index('id="face-palette"')
    # No form - the page stays read-only (colors are plain controls).
    assert "<form" not in html


def test_face_color_palette_labels_are_user_facing_copy(api_client, parent):
    robot = _robot(parent)
    html = api_client.get(controller_url(robot)).content.decode()
    text = _visible_text(html)
    for label in (
        "casing",
        "display",
        "eyes",
        "mouth",
        "glow",
        "amber",
        "blue",
        "pink",
        "green",
        "purple",
        "monochrome",
        "face colors",
        "reset to default",
    ):
        assert label in text, label


def test_robot_face_palette_is_session_only_and_validates_hex():
    source = _face_source()
    # Colors are session-only UI state: no persistence or storage sinks.
    for forbidden in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
        assert forbidden not in source, forbidden
    assert "PALETTE_DEFAULT" in source
    # Full (6-digit) and short (3-digit) hex values are accepted...
    assert "[0-9a-fA-F]{6}" in source
    assert "[0-9a-fA-F]{3}" in source
    # ...anything else is rejected with an accessible invalid state.
    assert 'setAttribute("aria-invalid"' in source
    # Six preset palettes plus reset-to-default.
    for preset in ("amber", "blue", "pink", "green", "purple", "mono"):
        assert f"{preset}:" in source, preset
    # Each color group drives its own channel - groups never alias.
    for channel in ("casingMat", "screenMat", "eyeColor", "mouthColor", "ringMat"):
        assert channel in source, channel


def test_robot_face_blink_overrides_eyes_and_reduced_motion_snaps():
    source = _face_source()
    assert "EXPRESSIONS.BLINK" in source
    assert "eyesClosed" in source
    assert "blinkT" in source
    assert "blinkTimer" in source
    # Under prefers-reduced-motion every damped value snaps instead of easing.
    assert "reduced ? 1 :" in source


def test_robot_face_animation_callback_is_not_shadowed():
    """Regression (found by live browser verification): the bezel mesh was
    named `frame`, and `var frame = ...` executed inside start() overwrote
    the hoisted function frame(dt, t) binding - every animation tick then
    threw `frame is not a function` and the canvas never rendered."""
    source = _face_source()
    assert "function frame(dt, t)" in source
    assert not re.search(r"\bvar\s+frame\b", source)
    assert not re.search(r"\blet\s+frame\b", source)


def test_controller_js_dispatches_confirmed_state_and_command_events():
    js_dir = Path(__file__).resolve().parents[2] / "static" / "js"
    source = (js_dir / "controller.js").read_text(encoding="utf-8")
    assert 'emitFaceEvent("state"' in source
    assert 'emitFaceEvent("command"' in source
    assert 'new CustomEvent("robotface:" + kind' in source
    # Control-panel contracts must remain byte-identical.
    assert "API.sendCommand(ROBOT_ID, action)" in source
    assert '$("offline-hint").hidden = data.online !== false;' in source
    assert "if (commandInFlight) return;" in source


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


# --- Connect Robot / pairing UI -------------------------------------------


def test_dashboard_exposes_connect_robot_action(api_client, parent):
    empty = api_client.get("/dashboard/").content.decode()
    # Header action + empty-state call to action, and counts stay intact.
    assert empty.count("data-connect-open") == 2
    assert "Connect Robot" in empty
    assert stat_value(empty, "total") == "0"

    _robot(parent, nickname="PairedBot")
    with_robot = api_client.get("/dashboard/").content.decode()
    assert with_robot.count("data-connect-open") == 1
    assert stat_value(with_robot, "total") == "1"


def test_dashboard_connect_dialog_markup_and_accessibility(api_client, parent):
    html = api_client.get("/dashboard/").content.decode()
    for marker in (
        'id="connect-dialog"',
        'aria-labelledby="connect-dialog-title"',
        'id="connect-code"',
        'for="connect-code"',
        'id="connect-nickname"',
        'for="connect-nickname"',
        'maxlength="8"',
        'maxlength="100"',
        'id="connect-error"',
        'role="alert"',
        'id="connect-success"',
        ">Pair robot<",
        ">Cancel<",
        "single-use",
        "15 minutes",
        "Shown once",
    ):
        assert marker in html, marker


def test_connect_dialog_is_dashboard_only(api_client, parent):
    robot = _robot(parent)
    html = api_client.get(controller_url(robot)).content.decode()
    assert "connect-dialog" not in html
    assert "data-connect-open" not in html


def test_dashboard_loads_api_js_before_dashboard_js(api_client, parent):
    html = api_client.get("/dashboard/").content.decode()
    api_at = html.find("js/api.js")
    dashboard_at = html.find("js/dashboard.js")
    assert api_at != -1, "api.js missing from dashboard"
    assert dashboard_at != -1, "dashboard.js missing from dashboard"
    assert api_at < dashboard_at


def test_dashboard_shows_never_connected_for_unseen_robots(api_client, parent):
    _robot(parent, nickname="SeenBot", last_seen=timezone.now())
    _robot(parent, nickname="FreshBot", last_seen=None)
    html = api_client.get("/dashboard/").content.decode()
    assert html.count("Never connected") == 1


def test_api_js_pair_source_contract():
    api_js = (
        Path(__file__).resolve().parents[2] / "static" / "js" / "api.js"
    ).read_text(encoding="utf-8")
    assert api_js.count("/api/v1/devices/pair/") == 1
    start = api_js.find("pair:")
    assert start != -1, "RobotAPI.pair missing"
    block = api_js[start : start + 400]
    assert 'request("POST", "/api/v1/devices/pair/"' in block
    assert "pairing_code" in block
    assert "nickname" in block
    for forbidden in ("parent_id", "owner_id", "user_id", "fetch(", "X-CSRFToken"):
        assert forbidden not in block, forbidden
    assert "parent" not in block


def test_dashboard_connect_js_source_contract():
    js = (
        Path(__file__).resolve().parents[2] / "static" / "js" / "dashboard.js"
    ).read_text(encoding="utf-8")
    assert "RobotAPI.pair" in js
    assert "/^[0-9a-fA-F]{24}$/.test" in js
    assert "toUpperCase()" in js
    assert "/dashboard/robots/" in js
    assert "body.detail" in js
    assert "body.pairing_code" in js
    assert "Your session may have expired. Refresh the page and try again." in js


def test_connect_dialog_source_holds_only_accepted_fields():
    source = (
        Path(__file__).resolve().parents[2]
        / "templates"
        / "dashboard"
        / "index.html"
    ).read_text(encoding="utf-8")
    start = source.find('id="connect-dialog"')
    end = source.find("</dialog>")
    assert start != -1 and end != -1
    block = source[start:end].lower()
    for forbidden in (
        "parent_id",
        "owner_id",
        "user_id",
        "localstorage",
        "sessionstorage",
        "mqtt",
    ):
        assert forbidden not in block, forbidden


def test_pairing_success_copy_does_not_claim_online():
    source = (
        Path(__file__).resolve().parents[2]
        / "templates"
        / "dashboard"
        / "index.html"
    ).read_text(encoding="utf-8")
    start = source.find('id="connect-success"')
    end = source.find("</dialog>")
    assert start != -1 and end != -1
    block = source[start:end].lower()
    assert "robot paired" in block
    assert "online" not in block


def test_connect_dialog_alphabet_hint_matches_backend(api_client, parent):
    # The documented alphabet must stay locked to the backend constant:
    # if the constant ever changes, this test fails and the hint needs an update.
    letters = [
        c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c not in PAIRING_CODE_ALPHABET
    ]
    assert letters == ["I", "L", "O"]
    assert [c for c in PAIRING_CODE_ALPHABET if c.isdigit()] == list("23456789")
    assert len(PAIRING_CODE_ALPHABET) == 31

    html = api_client.get("/dashboard/").content.decode()
    for marker in (
        "A-Z",
        "except I, L, and O",
        "digits 2-9",
        "single-use",
        "15 minutes",
        "Shown once",
    ):
        assert marker in html, marker


def test_connect_dialog_progress_status_element(api_client, parent):
    html = api_client.get("/dashboard/").content.decode()
    assert 'id="connect-progress"' in html
    assert 'role="status"' in html
    assert "still in progress" in html


def test_dashboard_pairing_inflight_guard_source_contract():
    """Source-contract pins for the single-flight guard (no JS runtime exists
    in this suite; the guard's runtime behaviour is verified separately by a
    one-off Node harness during the implementation session - not in CI)."""
    js = (
        Path(__file__).resolve().parents[2] / "static" / "js" / "dashboard.js"
    ).read_text(encoding="utf-8")

    submit_at = js.find('connectForm.addEventListener("submit"')
    assert submit_at != -1
    submit_block = js[submit_at:]

    # The guard is checked and set synchronously before the API call.
    assert "if (pairRequestActive) return;" in submit_block
    assert submit_block.index("pairRequestActive = true") < submit_block.index(
        "window.RobotAPI.pair("
    )

    # Exactly one guard release (inside finish, not the var declaration),
    # invoked by both callbacks.
    release_count = js.count("pairRequestActive = false") - js.count(
        "var pairRequestActive = false"
    )
    assert release_count == 1
    assert submit_block.count("finish();") == 2

    # Both callbacks drop stale responses via the generation token.
    assert submit_block.count("generation !== pairGeneration") == 2

    # Dialog reset is presentation-only: it must never clear the guard.
    reset_at = js.find("var resetDialog = function")
    close_at = js.find("var closeDialog")
    assert reset_at != -1 and close_at > reset_at
    reset_block = js[reset_at:close_at]
    assert "pairRequestActive" not in reset_block
    assert "pairGeneration" not in reset_block

    # Reopening during a pending request re-applies the busy UI + progress.
    open_at = js.find('querySelectorAll("[data-connect-open]")')
    assert open_at != -1
    open_block = js[open_at : js.find("querySelectorAll(\"[data-connect-close]\")")]
    assert "if (pairRequestActive)" in open_block
    assert "progressBox.hidden = false" in open_block
    assert "!codeInput.disabled" in open_block

    # Busy state disables editing and resubmission.
    busy_at = js.find("var setPairBusy = function")
    busy_block = js[busy_at : js.find("var resetDialog")]
    assert "codeInput.disabled = busy" in busy_block
    assert "nicknameInput.disabled = busy" in busy_block
    assert "submitButton.disabled = busy" in busy_block


def test_dashboard_static_refs_carry_asset_version(api_client, parent):
    """Panel asset URLs must carry the cache-busting version. Static responses
    have no Cache-Control, so an un-versioned dashboard.js could stay cached in
    a browser from before the pairing dialog existed - the button would render
    (fresh HTML) but do nothing (stale JS with no binding)."""
    assert settings.ASSET_VERSION
    html = api_client.get("/dashboard/").content.decode()
    for ref in (
        "/static/js/api.js?v=",
        "/static/js/dashboard.js?v=",
        "/static/css/app.css?v=",
    ):
        assert ref in html, ref
    assert f"/static/js/dashboard.js?v={settings.ASSET_VERSION}" in html
    assert f"/static/js/api.js?v={settings.ASSET_VERSION}" in html
    assert f"/static/css/app.css?v={settings.ASSET_VERSION}" in html


@override_settings(ASSET_VERSION="test-bump-123")
def test_asset_version_setting_flows_into_rendered_urls(api_client, parent):
    html = api_client.get("/dashboard/").content.decode()
    assert "/static/js/dashboard.js?v=test-bump-123" in html
    assert "/static/css/app.css?v=test-bump-123" in html


def test_connect_dialog_required_element_ids_exist_in_template():
    """Every element the dialog JS binds must exist in the template. A missing
    id would throw mid-click and the Connect Robot button would look dead."""
    js = (
        Path(__file__).resolve().parents[2] / "static" / "js" / "dashboard.js"
    ).read_text(encoding="utf-8")
    template = (
        Path(__file__).resolve().parents[2]
        / "templates"
        / "dashboard"
        / "index.html"
    ).read_text(encoding="utf-8")

    dialog_at = js.find("var dialog = document.getElementById")
    assert dialog_at != -1
    dialog_block = js[dialog_at : js.find("var pairRequestActive")]

    ids = re.findall(r'getElementById\("([^"]+)"\)', dialog_block)
    ids = [i for i in ids if i != "account-dropdown"]
    assert "connect-dialog" in ids
    assert "connect-form" in ids
    for element_id in ids:
        assert f'id="{element_id}"' in template, element_id

    # The submit button selector used by setPairBusy must match the template.
    assert 'button[type="submit"]' in js
    assert '<button type="submit"' in template


def test_dashboard_js_fails_visibly_when_required_elements_missing():
    """Incomplete dialog markup must surface a console.error and skip binding
    (fail visibly + safely) instead of throwing mid-click."""
    js = (
        Path(__file__).resolve().parents[2] / "static" / "js" / "dashboard.js"
    ).read_text(encoding="utf-8")

    guard_at = js.find("var requiredElements = [")
    assert guard_at != -1
    guard_block = js[guard_at : js.find("var pairRequestActive")]
    assert "console.error(" in guard_block
    assert "markup incomplete" in guard_block
    # Early return before any handler binding.
    assert "return;" in guard_block
    # Guard runs after the element lookups it validates.
    lookup_at = js.find('var codeInput = document.getElementById("connect-code")')
    assert 0 < lookup_at < guard_at

import logging
import uuid
from pathlib import Path

import pytest

from .test_device_auth import complete_auth, make_ec_keypair, register

pytestmark = pytest.mark.django_db

BACKEND_ROOT = Path(__file__).resolve().parents[2]
JS_DIR = BACKEND_ROOT / "static" / "js"
CONTROLLER_JS = JS_DIR / "controller.js"
CONTROLLER_TEMPLATE = BACKEND_ROOT / "templates" / "dashboard" / "controller.html"

MOCK_ASSETS = (
    "js/mocks/robotMock.js",
    "js/services/robotMockService.js",
    "js/services/robotCommandService.js",
    "js/ui/toast.js",
)


def _js_sources():
    return {path: path.read_text(encoding="utf-8") for path in JS_DIR.rglob("*.js")}


# --- Item H: the browser never talks MQTT/WebSocket directly ---------------


def test_browser_javascript_has_no_mqtt_or_websocket_clients():
    forbidden = (
        "mqtt://",
        "wss://",
        "mqtt.connect",
        "new websocket",
        "paho",
        "mqtt.js",
        "mosquitto",
    )
    for path, source in _js_sources().items():
        lowered = source.lower()
        for term in forbidden:
            assert term not in lowered, f"{term} found in {path.name}"


def test_browser_commands_go_only_through_the_rest_endpoint():
    source = CONTROLLER_JS.read_text(encoding="utf-8")
    assert "API.sendCommand(ROBOT_ID, action)" in source
    api_source = (JS_DIR / "api.js").read_text(encoding="utf-8")
    assert '"/api/v1/devices/"' in api_source
    assert '"command/"' in api_source


# --- Service-mode switch: real by default, mock only when explicitly set ---


def test_controller_page_declares_real_service_mode():
    html = CONTROLLER_TEMPLATE.read_text(encoding="utf-8")
    assert 'data-service-mode="real"' in html
    for asset in MOCK_ASSETS:
        assert asset in html, f"{asset} must stay shipped for local preview"


def test_controller_js_defaults_to_real_and_never_falls_back_to_mock():
    source = CONTROLLER_JS.read_text(encoding="utf-8")
    assert 'SERVICE_MODE === "mock"' in source, "real must be the default mode"
    assert "failServiceUnavailable" in source, (
        "missing explicit error path when the live API is unavailable"
    )
    assert "var API = IS_MOCK ? window.RobotMockService : window.RobotAPI;" in source


def test_real_mode_reads_through_robot_api():
    source = CONTROLLER_JS.read_text(encoding="utf-8")
    assert "API.getSummary(ROBOT_ID)" in source
    assert "API.getState(ROBOT_ID)" in source
    assert "window.RobotAPI" in source


# --- Items N and O: truthful real-mode command states ----------------------


def test_real_mode_reports_failure_without_fake_success():
    source = CONTROLLER_JS.read_text(encoding="utf-8")
    assert 'setCommandStatus(label + " could not be sent.", "is-error")' in source
    assert '"Command could not be sent"' in source
    assert 'setCommandStatus(label + " sent.", "is-ok")' in source
    assert '"Command " + label.toUpperCase() + " sent"' in source


def test_queued_copy_exists_only_in_the_mock_branch():
    source = CONTROLLER_JS.read_text(encoding="utf-8")
    send_command = source.split("function sendCommand(button) {", 1)
    assert len(send_command) == 2, "sendCommand not found"
    branches = send_command[1].split("if (IS_MOCK) {", 1)
    assert len(branches) == 2, "mock branch not found inside sendCommand"
    regions = branches[1].split("return;", 1)
    assert len(regions) == 2, "mock branch does not end with return"
    mock_region, real_region = regions
    assert "queued" in mock_region
    assert "queued" not in real_region, (
        "real mode must never claim a command was queued"
    )


def test_command_in_flight_guard_disables_buttons():
    source = CONTROLLER_JS.read_text(encoding="utf-8")
    assert "if (commandInFlight) return;" in source
    assert "commandInFlight = true;" in source
    assert "setCommandsDisabled(true);" in source
    assert "setCommandsDisabled(false);" in source


# --- Item M (UI): never-reported state renders as such ----------------------


def test_null_online_state_renders_as_never_reported():
    source = CONTROLLER_JS.read_text(encoding="utf-8")
    assert '"Never reported"' in source
    assert '"Not available"' in source


def test_offline_warning_never_claims_offline_for_unreported_state():
    """online=null (never reported) must not show the 'Robot is offline'
    banner — only an actual online:false may."""
    source = CONTROLLER_JS.read_text(encoding="utf-8")
    assert '$("offline-hint").hidden = data.online !== false;' in source
    assert '$("offline-hint").hidden = online;' not in source


def test_template_has_no_hardcoded_trend_or_fabricated_telemetry_graphic():
    """Real mode must not draw fake trend lines for values the robot
    never reported (mock data lives only in the mock service)."""
    html = CONTROLLER_TEMPLATE.read_text(encoding="utf-8")
    assert 'points="0,16' not in html, "hardcoded fake trend polyline found"
    assert 'id="state-uptime-sub"' in html, "honest uptime copy must remain"


# --- Item J: pairing code and device JWT never reach the logs --------------


def test_pairing_code_and_device_jwt_never_logged(api_client, caplog):
    private_key, pem = make_ec_keypair()
    device_id = str(uuid.uuid4())

    with caplog.at_level(logging.DEBUG):
        registration = register(api_client, device_id, pem)
        token = complete_auth(api_client, device_id, private_key)["access_token"]

    pairing_code = registration["pairing_code"]
    assert pairing_code
    assert token

    assert pairing_code not in caplog.text
    assert token not in caplog.text
    for record in caplog.records:
        message = record.getMessage()
        assert pairing_code not in message, record.name
        assert token not in message, record.name
        rendered_args = " ".join(str(arg) for arg in record.args or ())
        assert pairing_code not in rendered_args, record.name
        assert token not in rendered_args, record.name

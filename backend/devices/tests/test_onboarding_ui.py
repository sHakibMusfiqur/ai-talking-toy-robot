import secrets
import uuid
from pathlib import Path
from urllib.parse import unquote

import pytest
from django.conf import settings
from django.test import override_settings

from devices.models import DeviceClaimTicket, Robot

from .helpers import setup_parent
from .test_claim import issue_ticket, mint_device_token

pytestmark = pytest.mark.django_db

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ONBOARDING_JS = BACKEND_ROOT / "static" / "js" / "onboarding.js"
ONBOARDING_TEMPLATE = (
    BACKEND_ROOT / "templates" / "dashboard" / "onboarding.html"
)

FORBIDDEN_PAGE_WORDS = (
    "parent_id",
    "owner_id",
    "user_id",
    "localstorage",
    "sessionstorage",
    "mqtt",
)
FORBIDDEN_HARDWARE_TERMS = ("servo", "pwm", "gpio", "motor", "voltage")


def _robot(parent, nickname):
    return Robot.objects.create(
        device_id=str(uuid.uuid4()),
        parent=parent,
        nickname=nickname,
        firmware_version="1.2.3",
    )


def _confirm_html(client, ticket=None):
    ticket = ticket or secrets.token_urlsafe(32)
    response = client.get(f"/onboarding/claim/?ticket={ticket}")
    assert response.status_code == 200, response.content
    return response.content.decode()


# --- Access control --------------------------------------------------------


def test_onboarding_pages_require_login(api_client):
    for url in ("/onboarding/", f"/onboarding/claim/?ticket={'A' * 43}"):
        response = api_client.get(url)
        assert response.status_code == 302
        assert response.url.startswith("/login/")
        assert "next=" in unquote(response.url)
        assert unquote(response.url).split("next=")[-1] == url


# --- Instructions step -----------------------------------------------------


def test_onboarding_instructions_step_renders(api_client):
    setup_parent(api_client, "steps@example.com")
    response = api_client.get("/onboarding/")
    assert response.status_code == 200
    html = response.content.decode()

    assert "Connect your robot to Wi-Fi" in html
    assert "RoboPanel-Setup-" in html
    assert "http://192.168.4.1" in html
    assert "single-use" in html
    assert "10 minutes" in html
    assert 'id="claim-confirm"' not in html
    assert 'id="claim-success"' in html  # hidden shell for every step

    assert "No pairing code?" not in html  # dialog entry lives on dashboard only


def test_onboarding_pages_show_no_hardware_internals(api_client):
    from devices.tests.test_dashboard import _visible_text

    setup_parent(api_client, "plain-lang@example.com")
    for url in ("/onboarding/", "/onboarding/claim/?ticket=" + "A" * 43):
        html = api_client.get(url).content.decode()
        text = _visible_text(html)
        for term in FORBIDDEN_HARDWARE_TERMS:
            assert term not in text, f"{term} visible on {url}"


def test_onboarding_pages_never_list_or_discover_robots(api_client):
    parent = setup_parent(api_client, "no-list@example.com")
    _robot(parent, "SECRET-BUDDY-ONE")
    _robot(parent, "SECRET-BUDDY-TWO")

    for url in ("/onboarding/", "/onboarding/claim/?ticket=" + "A" * 43):
        html = api_client.get(url).content.decode()
        assert "SECRET-BUDDY-ONE" not in html
        assert "SECRET-BUDDY-TWO" not in html
        assert "/api/v1/devices/" not in html


# --- Confirm step (rendered from the robot's setup link) -------------------


def test_onboarding_claim_confirm_step_renders_ticket(api_client):
    setup_parent(api_client, "confirm@example.com")
    ticket = secrets.token_urlsafe(32)
    html = _confirm_html(api_client, ticket)

    assert 'id="claim-confirm"' in html
    assert f'data-claim-ticket="{ticket}"' in html
    assert 'id="claim-submit"' in html
    assert 'id="claim-nickname"' in html
    assert 'id="claim-error"' in html
    assert 'id="claim-success"' in html
    assert "Add this robot to your account?" in html
    assert "Link not valid" not in html


def test_onboarding_claim_get_never_consumes_the_ticket(api_client):
    registration, token = mint_device_token(api_client)
    ticket = issue_ticket(api_client, token)
    setup_parent(api_client, "read-only@example.com")

    html = _confirm_html(api_client, ticket)
    assert f'data-claim-ticket="{ticket}"' in html

    row = DeviceClaimTicket.objects.get(robot__pk=registration["id"])
    assert row.consumed_at is None
    robot = Robot.objects.get(pk=registration["id"])
    assert robot.pairing_status == Robot.PAIRING_UNPAIRED
    assert robot.parent_id is None


def test_onboarding_get_never_leaks_ticket_validity(api_client):
    setup_parent(api_client, "no-oracle@example.com")
    unknown = secrets.token_urlsafe(32)

    valid_shape_html = _confirm_html(api_client, unknown)
    assert 'id="claim-confirm"' in valid_shape_html
    assert "Link not valid" not in valid_shape_html

    bad_shape = api_client.get("/onboarding/claim/?ticket=abc")
    assert bad_shape.status_code == 200
    bad_html = bad_shape.content.decode()
    assert "Link not valid" in bad_html
    assert 'id="claim-confirm"' not in bad_html
    assert 'data-claim-ticket="' not in bad_html


# --- Page hygiene ----------------------------------------------------------


def test_onboarding_pages_never_touch_web_storage(api_client):
    setup_parent(api_client, "storage@example.com")
    pages = [
        api_client.get("/onboarding/").content.decode(),
        _confirm_html(api_client),
        _confirm_html(api_client, "not-a-ticket"),
    ]
    for html in pages:
        lowered = html.lower()
        assert "localstorage" not in lowered
        assert "sessionstorage" not in lowered

    source = ONBOARDING_JS.read_text(encoding="utf-8").lower()
    assert "localstorage" not in source
    assert "sessionstorage" not in source
    assert "mqtt" not in source


def test_onboarding_pages_carry_no_forbidden_identifiers(api_client):
    setup_parent(api_client, "identifiers@example.com")
    for html in (
        api_client.get("/onboarding/").content.decode(),
        _confirm_html(api_client),
    ):
        lowered = html.lower()
        for word in FORBIDDEN_PAGE_WORDS:
            assert word not in lowered, f"{word} found in onboarding page"


def test_onboarding_scripts_load_api_first_with_asset_version(api_client):
    setup_parent(api_client, "script-order@example.com")
    html = _confirm_html(api_client)
    api_at = html.find("/static/js/api.js")
    onboarding_at = html.find("/static/js/onboarding.js")
    assert -1 not in (api_at, onboarding_at)
    assert api_at < onboarding_at
    assert f"/static/js/api.js?v={settings.ASSET_VERSION}" in html
    assert f"/static/js/onboarding.js?v={settings.ASSET_VERSION}" in html


def test_onboarding_claim_success_copy_never_claims_online(api_client):
    setup_parent(api_client, "success-copy@example.com")
    html = _confirm_html(api_client)
    start = html.index('id="claim-success"')
    end = html.index("</section>", start)
    block = html[start:end].lower()
    assert "robot added" in block
    assert "online" not in block


def test_onboarding_template_and_script_sources_agree(api_client):
    """The ids the claim script binds must exist in the rendered page."""
    template = ONBOARDING_TEMPLATE.read_text(encoding="utf-8")
    source = ONBOARDING_JS.read_text(encoding="utf-8")
    for element_id in (
        "claim-confirm",
        "claim-submit",
        "claim-error",
        "claim-nickname",
        "claim-success",
        "claim-success-name",
    ):
        assert f'id="{element_id}"' in template, element_id
        assert f'getElementById("{element_id}")' in source, element_id
    assert 'getAttribute("data-claim-ticket")' in source
    assert 'replaceState({}, "", "/onboarding/claim/")' in source


# --- Dashboard wizard entry ------------------------------------------------


def test_dashboard_offers_wifi_setup_wizard_entry(api_client):
    setup_parent(api_client, "wizard@example.com")
    html = api_client.get("/dashboard/").content.decode()
    assert "No pairing code?" in html
    assert "Connect with Wi-Fi setup" in html
    assert 'href="/onboarding/"' in html
    # The entry is a plain link: it must not disturb the dialog bindings.
    assert html.count("data-connect-open") == 2


@override_settings(ASSET_VERSION="test-bump-456")
def test_onboarding_static_refs_follow_asset_version(api_client):
    setup_parent(api_client, "asset-version@example.com")
    html = _confirm_html(api_client)
    assert "/static/js/api.js?v=test-bump-456" in html
    assert "/static/js/onboarding.js?v=test-bump-456" in html

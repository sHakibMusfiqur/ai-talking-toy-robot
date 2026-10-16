import re
from pathlib import Path

from django.urls import reverse

FIRMWARE_DIR = Path(__file__).resolve().parents[3] / "firmware"
MAIN_DIR = FIRMWARE_DIR / "main"
KCONFIG = MAIN_DIR / "Kconfig.projbuild"
CMAKE = MAIN_DIR / "CMakeLists.txt"
SETUP_PAGE = MAIN_DIR / "www" / "setup.html"


def _source(name):
    return (MAIN_DIR / name).read_text(encoding="utf-8")


def _kconfig_default(block):
    match = re.search(
        rf"config {block}\n.*?default \"?(.*?)\"?\n",
        KCONFIG.read_text(encoding="utf-8"),
        re.S,
    )
    assert match, f"missing Kconfig block {block}"
    return match.group(1)


# --- Firmware <-> backend URL contract -------------------------------------


def test_claim_and_onboarding_routes_match_firmware_urls():
    assert reverse("api-device-claim-ticket") == "/api/v1/devices/claim-tickets/"
    assert reverse("api-device-claim-ticket-raw") == "/api/v1/devices/claim-tickets"
    assert reverse("api-robot-claim") == "/api/v1/devices/claim/"
    assert reverse("onboarding") == "/onboarding/"
    assert reverse("onboarding-claim") == "/onboarding/claim/"

    client_source = _source("backend_client.c")
    assert (
        '#define DEVICE_CLAIM_TICKETS_PATH "/api/v1/devices/claim-tickets/"'
        in client_source
    )
    assert '#define CLAIM_URL_PATH "/onboarding/claim/"' in client_source
    assert 'CLAIM_URL_PATH "?ticket=%s"' in client_source


def test_firmware_posts_empty_body_and_refreshes_on_401_once():
    """Approved contract: POST {} with the device Bearer JWT -> 201
    {ticket, expires_in}; on 401 refresh the token and retry exactly once;
    any other status is a provisioning failure (no ticket, no link)."""
    client_source = _source("backend_client.c")
    claim_fn = client_source[
        client_source.index("esp_err_t backend_create_claim_ticket") : client_source.index(
            "esp_err_t backend_claim_url"
        )
    ]
    assert "post_json(DEVICE_CLAIM_TICKETS_PATH, body, s_token" in claim_fn
    # Approved contract detail: the body is an empty JSON object ({}).
    assert "cJSON_CreateObject()" in claim_fn
    assert "cJSON_Add" not in claim_fn
    assert "cJSON_PrintUnformatted" in claim_fn
    assert "status == 401 && attempt == 0" in claim_fn
    assert "backend_refresh_token()" in claim_fn
    assert "status != 201" in claim_fn
    # The ticket value itself is copied out, never logged.
    assert "claim ticket issued" in client_source


def test_setup_page_only_offers_absolute_http_claim_links():
    page = SETUP_PAGE.read_text(encoding="utf-8")
    assert '/^https?:\\/\\//i.test(st.claim_url)' in page
    placeholders = set(re.findall(r"__[A-Z_]+__", page))
    assert placeholders == {
        "__PROVISION_TOKEN__",
        "__PROVISION_REASON__",
        "__SAVED_SSID__",
    }


# --- Provisioning HTTP surface ---------------------------------------------


def test_setup_write_endpoints_require_the_provision_token():
    source = _source("wifi_provision.c")
    assert '"X-Provision-Token"' in source
    assert "static bool token_ok(httpd_req_t *req)" in source

    wifi_handler = source[
        source.index("static esp_err_t handle_wifi_submit") : source.index(
            "static esp_err_t handle_status"
        )
    ]
    finalize_handler = source[
        source.index("static esp_err_t handle_finalize") : source.index(
            "static void setup_indication_init"
        )
    ]
    assert "token_ok(req)" in wifi_handler
    assert "token_ok(req)" in finalize_handler

    # Registered endpoints are exactly the documented surface.
    for uri in ('"/"', '"/api/scan"', '"/api/wifi"', '"/api/status"', '"/api/finalize"'):
        assert f".uri = {uri}" in source


def test_status_response_never_contains_credential_fields():
    source = _source("wifi_provision.c")
    status_handler = source[
        source.index("static esp_err_t handle_status") : source.index(
            "static esp_err_t handle_finalize"
        )
    ]
    emitted = re.findall(r'cJSON_AddStringToObject\(root, "([^"]+)"', status_handler)
    assert set(emitted) <= {"state", "reason", "claim_url"}
    for word in ("password", "passwd", "secret", "token"):
        assert word not in emitted


def test_setup_access_point_is_wpa2_only():
    source = _source("wifi_provision.c")
    assert "ap_cfg.ap.authmode = WIFI_AUTH_WPA2_PSK;" in source
    assert "WIFI_AUTH_OPEN" not in source


def test_setup_page_never_touches_web_storage_or_mqtt():
    page = SETUP_PAGE.read_text(encoding="utf-8").lower()
    for word in (
        "localstorage",
        "sessionstorage",
        "mqtt",
        "parent_id",
        "owner_id",
        "user_id",
    ):
        assert word not in page, word


# --- Kconfig: no invented hardware -----------------------------------------


def test_setup_kconfig_defaults_invent_no_gpio_or_shared_secret():
    assert _kconfig_default("ROBOT_SETUP_AP_SSID_PREFIX") == "RoboPanel-Setup"
    assert _kconfig_default("ROBOT_SETUP_AP_PASSWORD") == ""
    assert _kconfig_default("ROBOT_WIFI_SETUP_FALLBACK_MS") == "60000"
    assert _kconfig_default("ROBOT_FACTORY_RESET_GPIO") == "-1"
    assert _kconfig_default("ROBOT_SETUP_LED_GPIO") == "-1"


def test_setup_indicator_and_factory_reset_stay_optional_in_source():
    provision = _source("wifi_provision.c")
    assert "if (CONFIG_ROBOT_SETUP_LED_GPIO < 0)" in provision
    app_main = _source("app_main.c")
    assert "if (CONFIG_ROBOT_FACTORY_RESET_GPIO < 0)" in app_main


# --- Build + boot wiring ----------------------------------------------------


def test_provisioning_module_is_wired_into_the_build():
    cmake = CMAKE.read_text(encoding="utf-8")
    assert "wifi_provision.c" in cmake
    assert "www/setup.html" in cmake
    assert "esp_http_server" in cmake
    assert "_binary_www_setup_html_start" in _source("wifi_provision.c")

    headers = _source("wifi_provision.h")
    assert (
        "void provision_run(const char *reason, bool wifi_already_started);"
        in headers
    )


def test_setup_mode_entry_conditions_match_the_approved_plan():
    app_main = _source("app_main.c")
    assert "#define AUTH_FAILURE_PROVISION_THRESHOLD 3" in app_main
    assert 'provision_run("no_credentials", false);' in app_main
    assert 'provision_run("auth_failure", true);' in app_main
    assert "wifi_wait_connected(CONFIG_ROBOT_WIFI_SETUP_FALLBACK_MS)" in app_main
    assert "wifi_auth_failure_count() >= AUTH_FAILURE_PROVISION_THRESHOLD" in app_main
    assert "check_factory_reset();" in app_main

    wifi = _source("wifi.c")
    assert "bool wifi_has_saved_credentials(void)" in wifi
    assert "wifi_erase_credentials" in wifi


def test_setup_banner_shows_key_and_legacy_pairing_fallback():
    provision = _source("wifi_provision.c")
    assert '"   Setup key: %s\\n"' in provision
    # When the claim link could not be created, the classic single-use
    # pairing code is printed from the provisioning banner as well
    # (the two-printer allowlist is enforced in test_firmware_contract).
    assert "Pairing code (single use)" in provision
    assert "Enter it in the web dashboard to link this robot." in provision


def test_home_wifi_secret_is_stored_never_rendered_to_the_page():
    provision = _source("wifi_provision.c")
    # Stored under NVS key "setup_pass"/"pass" for reconnect — but the
    # rendered page placeholders (checked above) never carry it.
    assert "nvs_set_str" in provision or "wifi_save_credentials" in _source("wifi.c")
    assert '"__WIFI_PASS__"' not in SETUP_PAGE.read_text(encoding="utf-8")

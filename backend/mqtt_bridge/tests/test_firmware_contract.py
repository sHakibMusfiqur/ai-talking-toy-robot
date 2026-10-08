import re
import shutil
import subprocess
from pathlib import Path

import pytest
from core.semantic_actions import SEMANTIC_ACTIONS
from mqtt_bridge import payloads

FIRMWARE_DIR = Path(__file__).resolve().parents[3] / "firmware"
MAIN_DIR = FIRMWARE_DIR / "main"

REQUIRED_FILES = (
    "CMakeLists.txt",
    ".gitignore",
    "sdkconfig.defaults",
    "main/CMakeLists.txt",
    "main/Kconfig.projbuild",
    "main/app_main.c",
    "main/identity.c",
    "main/identity.h",
    "main/wifi.c",
    "main/wifi.h",
    "main/backend_client.c",
    "main/backend_client.h",
    "main/robot_mqtt.c",
    "main/robot_mqtt.h",
    "main/telemetry.c",
    "main/telemetry.h",
    "main/actions.c",
    "main/actions.h",
)

# Security item I: patterns that disable or skip certificate validation.
FORBIDDEN_TLS_PATTERNS = (
    "setinsecure",
    "disable_ssl_cert_validation",
    "ssl_verify_none",
    "allow_insecure",
    "skip_cert_common_name_check",
    "accept_all_cert",
    "verify_none",
    "no_certificate_validation",
    "certificate_validation_disabled",
)

# Security item J: words that must never appear in a firmware log line
# (values are never logged; log copy avoids these words entirely).
CREDENTIAL_LOG_WORDS = (
    "token",
    "password",
    "passwd",
    "secret",
    "jwt",
    "bearer",
    "authorization",
)


def _all_sources():
    files = []
    for pattern in ("*.c", "*.h", "CMakeLists.txt", "Kconfig.projbuild", "sdkconfig.defaults", ".gitignore"):
        files.extend(FIRMWARE_DIR.rglob(pattern))
    return [path for path in files if path.is_file()]


def _c_sources():
    return sorted(MAIN_DIR.glob("*.c"))


def _read(path):
    return path.read_text(encoding="utf-8")


def _log_statements(path):
    """Yield (first_line, statement) for every logging call (ESP_LOG*
    macro or printf) in the file. Continuation lines are joined until
    the terminating ';' that sits outside a string literal, so wrapped
    format strings and arguments are scanned as part of the statement.
    Non-logging calls (snprintf/strlcpy/assignment) are not statements
    and are correctly excluded — the scan targets what actually reaches
    the serial console."""
    source = _read(path)
    statements = []
    for match in re.finditer(r"\b(ESP_LOG[A-Z0-9_]*|printf)\s*\(", source):
        start = match.start()
        line_number = source.count("\n", 0, start) + 1
        index = match.end()
        in_string = False
        escaped = False
        while index < len(source):
            char = source[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            else:
                if char == '"':
                    in_string = True
                elif char == ";":
                    index += 1
                    break
            index += 1
        statements.append((line_number, source[start:index]))
    return statements


def test_firmware_project_layout_is_complete():
    assert FIRMWARE_DIR.is_dir(), "firmware/ project missing"
    for relative in REQUIRED_FILES:
        assert (FIRMWARE_DIR / relative).is_file(), f"missing {relative}"


def test_firmware_gitignore_covers_build_and_local_secrets():
    content = _read(FIRMWARE_DIR / ".gitignore")
    for entry in ("build/", "sdkconfig", "sdkconfig.local"):
        assert entry in content, f"{entry} not ignored"


def test_firmware_semantic_actions_match_backend_contract():
    source = _read(MAIN_DIR / "actions.c")
    match = re.search(r"kSemanticActions\[\]\s*=\s*\{(.*?)\};", source, re.S)
    assert match, "kSemanticActions array not found in actions.c"
    firmware_actions = set(re.findall(r'"([A-Z_]+)"', match.group(1)))
    assert firmware_actions == set(SEMANTIC_ACTIONS), (
        f"firmware actions {sorted(firmware_actions)} != "
        f"backend contract {sorted(SEMANTIC_ACTIONS)}"
    )


def test_firmware_matches_actions_case_insensitively_but_logs_canonical_name():
    source = _read(MAIN_DIR / "actions.c")
    assert "toupper(" in source, "case-insensitive matching missing"
    assert '"Received semantic action: %s"' in source


def test_firmware_has_program_for_every_semantic_action():
    source = _read(MAIN_DIR / "actions.c")
    for action in SEMANTIC_ACTIONS:
        assert f'"{action}"' in source, action


def test_firmware_subscribes_only_to_its_own_topics():
    for path in _all_sources():
        content = _read(path)
        assert "robots/#" not in content, f"wildcard in {path.name}"
        assert "robots/+" not in content, f"wildcard in {path.name}"

    source = _read(MAIN_DIR / "robot_mqtt.c")
    subscriptions = re.findall(r"esp_mqtt_client_subscribe\(\s*s_client,\s*([^,]+),", source)
    assert len(subscriptions) == 4, subscriptions
    for arg in subscriptions:
        assert arg.strip().startswith("s_topics."), arg


def test_firmware_topic_shapes_match_backend_contract():
    source = _read(MAIN_DIR / "robot_mqtt.c")
    for shape in (
        "robots/%s/status",
        "robots/%s/heartbeat",
        "robots/%s/event",
        "robots/%s/command",
        "robots/%s/config/notify",
        "robots/%s/ota/notify",
        "robots/%s/content/notify",
    ):
        assert shape in source, f"topic shape missing: {shape}"


def test_firmware_publish_qos_and_retain_match_contract():
    source = _read(MAIN_DIR / "robot_mqtt.c")
    assert re.search(r"publish_to\(\s*s_topics\.status,\s*1,\s*true,", source)
    assert re.search(r"publish_to\(\s*s_topics\.heartbeat,\s*0,\s*false,", source)
    assert re.search(r"publish_to\(\s*s_topics\.event,\s*1,\s*false,", source)
    assert "publish_to(s_topics.command" not in source, "firmware must never publish commands"


def test_firmware_lwt_is_offline_retained_status():
    source = _read(MAIN_DIR / "robot_mqtt.c")
    assert '#define LWT_PAYLOAD "{\\"online\\":false}"' in source
    assert ".session.last_will.qos = 1" in source
    assert ".session.last_will.retain = true" in source


def test_firmware_status_keys_stay_within_contract():
    allowed = set(payloads.ALLOWED_STATUS_FIELDS)
    for line in _read(MAIN_DIR / "telemetry.c").splitlines():
        if '\\"online\\":' not in line:
            continue
        keys = set(re.findall(r'\\"([a-z_]+)\\":', line))
        assert keys, f"no keys parsed from status line: {line}"
        assert keys <= allowed, f"status keys outside contract: {keys - allowed}"


def test_firmware_heartbeat_payload_is_frozen_empty_object():
    header = _read(MAIN_DIR / "telemetry.h")
    assert '#define TELEMETRY_HEARTBEAT_PAYLOAD "{}"' in header
    source = _read(MAIN_DIR / "telemetry.c")
    assert "robot_mqtt_publish_heartbeat(TELEMETRY_HEARTBEAT_PAYLOAD" in source
    assert re.search(r"publish_heartbeat\([^)]*\{", source) is None, (
        "heartbeat must only ever send the frozen {} payload"
    )


def test_firmware_event_envelope_has_only_contract_keys():
    for path in (MAIN_DIR / "telemetry.c", MAIN_DIR / "actions.c"):
        source = _read(path)
        for line in source.splitlines():
            if '"event_type"' not in line:
                continue
            keys = set(re.findall(r'\\"([a-z_]+)\\":', line))
            if not keys:
                continue
            assert keys <= {"event_type", "payload"}, f"{path.name}: {keys}"


def test_firmware_has_no_tls_bypass_patterns():
    for path in _all_sources():
        lowered = _read(path).lower()
        for pattern in FORBIDDEN_TLS_PATTERNS:
            assert pattern not in lowered, f"{pattern} found in {path.name}"


def test_firmware_tls_is_on_by_default():
    kconfig = _read(MAIN_DIR / "Kconfig.projbuild")
    for block in ("ROBOT_BACKEND_USE_TLS", "ROBOT_MQTT_USE_TLS"):
        match = re.search(
            rf"config {block}\n.*?default (y|n)", kconfig, re.S
        )
        assert match, f"{block} default not found"
        assert match.group(1) == "y", f"{block} must default to y"


def test_firmware_tls_always_has_a_trust_source():
    """TLS enabled means a real verification source (pinned CA or the
    certificate bundle) — never a validation-less connection."""
    for path in (MAIN_DIR / "backend_client.c", MAIN_DIR / "robot_mqtt.c"):
        source = _read(path)
        has_ca_pin = "CONFIG_ROBOT_" in source and "CA_PEM" in source
        assert has_ca_pin, path.name
        assert "crt_bundle" in source, f"{path.name}: no trust-source fallback"


def test_firmware_never_logs_credentials():
    """Scan every complete ESP_LOG*/printf() statement — joining wrapped
    lines up to the terminating semicolon (tracked outside string
    literals) so a credential word or argument on a continuation line
    cannot evade the check. Same word list, statement-level coverage:
    strictly stronger than a line-by-line scan, never weaker."""
    for path in _c_sources():
        for number, statement in _log_statements(path):
            lowered = statement.lower()
            for word in CREDENTIAL_LOG_WORDS:
                assert word not in lowered, (
                    f"{path.name}:{number} log statement contains "
                    f"credential word '{word}': {' '.join(statement.split())}"
                )


def test_pairing_code_is_printed_exactly_once():
    """The pairing code is the one credential that must reach the serial
    console (first-boot pairing). It may be formatted by exactly one log
    statement in the whole firmware — the single-use banner."""
    printers = []
    for path in _c_sources():
        for number, statement in _log_statements(path):
            if "pairing_code" in statement or "Pairing code" in statement:
                printers.append((path.name, number, statement))
    assert len(printers) == 1, (
        f"pairing code printed by {len(printers)} log statements: "
        f"{[(n, l) for n, l, _ in printers]}"
    )
    name, _, statement = printers[0]
    assert name == "app_main.c", f"unexpected pairing printer: {name}"
    assert "printf(" in statement and "single use" in statement, (
        "pairing code must come from the single-use banner: "
        + " ".join(statement.split())
    )


def test_tracked_sdkconfig_contains_no_secrets():
    content = _read(FIRMWARE_DIR / "sdkconfig.defaults")
    assert "CONFIG_ROBOT_WIFI_PASSWORD" not in content
    assert "CONFIG_ROBOT_WIFI_SSID" not in content
    assert "CA_PEM" not in content
    assert "-----BEGIN" not in content
    config_lines = [
        line
        for line in content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    for line in config_lines:
        for word in ("password", "secret", "token", "key="):
            assert word not in line.lower(), f"tracked config leaks: {line}"


def test_kconfig_credentials_default_to_empty():
    kconfig = _read(MAIN_DIR / "Kconfig.projbuild")
    for block in ("ROBOT_WIFI_SSID", "ROBOT_WIFI_PASSWORD", "ROBOT_BACKEND_CA_PEM", "ROBOT_MQTT_CA_PEM"):
        match = re.search(rf"config {block}\n.*?default \"(.*?)\"", kconfig, re.S)
        assert match, f"{block} default not found"
        assert match.group(1) == "", f"{block} must default to empty"


def test_identity_is_uuid_v4_with_persistent_keypair():
    source = _read(MAIN_DIR / "identity.c")
    assert "generate_uuid_v4" in source
    assert "0x40" in source and "0x80" in source, "UUID version/variant bits missing"
    assert "nvs_set_str(nvs, NVS_KEY_PRIVATE_KEY" in source
    assert "mbedtls_pk_parse_key" in source, "stored key must be reloaded, not regenerated"


def test_identity_signs_with_ecdsa_sha256():
    source = _read(MAIN_DIR / "identity.c")
    assert "mbedtls_sha256(" in source
    assert "mbedtls_pk_sign(" in source
    assert "MBEDTLS_MD_SHA256" in source
    assert "mbedtls_base64_encode(" in source


def test_joint_limits_are_defined_and_enforced():
    source = _read(MAIN_DIR / "actions.c")
    assert "static const joint_limits_t kJointLimits[]" in source
    assert "clamp_to_limits(joint, target_deg)" in source, (
        "apply_move must clamp through the limits table"
    )
    entries = re.findall(r'\{"([a-z_]+)",\s*(-?\d+),\s*(-?\d+)\}', source)
    limits = {joint: (int(low), int(high)) for joint, low, high in entries}
    assert len(limits) >= 4, f"joint limits parsed: {limits}"
    for joint, (low, high) in limits.items():
        assert low <= high, f"inverted limits for {joint}"


def test_command_payload_is_parsed_and_unknown_actions_rejected():
    source = _read(MAIN_DIR / "actions.c")
    assert 'cJSON_GetObjectItem(parsed, "action")' in source
    assert "match_semantic_action" in source
    assert '"unknown_action"' in source
    assert '"invalid_payload"' in source


def test_app_main_tracks_the_required_connection_states():
    source = _read(MAIN_DIR / "app_main.c")
    for state in (
        "WIFI_DISCONNECTED",
        "WIFI_CONNECTED",
        "MQTT_DISCONNECTED",
        "MQTT_CONNECTED",
    ):
        assert state in source, state
    assert "backoff_next" in source, "bounded backoff missing"


@pytest.mark.skipif(
    shutil.which("idf.py") is None,
    reason="ESP-IDF toolchain (idf.py) not available — firmware build NOT TESTED",
)
def test_firmware_builds_with_espidf(tmp_path):
    result = subprocess.run(
        ["idf.py", "build"],
        cwd=FIRMWARE_DIR,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]

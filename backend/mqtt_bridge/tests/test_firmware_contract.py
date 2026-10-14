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
    "main/audio_capture.c",
    "main/audio_capture.h",
    "main/conversation.c",
    "main/conversation.h",
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


# --- Phase 12 PTT capture + bounded upload (source-contract suite) ---------

PROTECTED_LOG_WORDS = (
    "child_profile",
    "idempotency",
    "audio_bytes",
    "base64",
    "pcm_dst",
)


def test_firmware_psram_is_enabled_in_defaults():
    content = _read(FIRMWARE_DIR / "sdkconfig.defaults")
    assert "CONFIG_SPIRAM=y" in content, "PSRAM support must be enabled"
    assert "CONFIG_SPIRAM_MODE" not in content, (
        "PSRAM quad/octal mode must stay a local sdkconfig decision "
        "until the physical module is confirmed"
    )


def test_firmware_requires_i2s_and_psram_components():
    content = _read(MAIN_DIR / "CMakeLists.txt")
    requires = content.split("REQUIRES", 1)[1]
    assert re.search(r"^\s*driver\s*$", requires, re.M), "driver missing"
    assert re.search(r"^\s*esp_psram\s*$", requires, re.M), "esp_psram missing"
    assert '"audio_capture.c"' in content
    assert '"conversation.c"' in content


def test_kconfig_ptt_defaults_to_disabled():
    kconfig = _read(MAIN_DIR / "Kconfig.projbuild")
    match = re.search(
        r"config ROBOT_PTT_GPIO\n.*?default (-?\d+)", kconfig, re.S
    )
    assert match, "ROBOT_PTT_GPIO not found"
    assert match.group(1) == "-1", "PTT must default to disabled"


def test_kconfig_mic_pins_default_to_unset():
    kconfig = _read(MAIN_DIR / "Kconfig.projbuild")
    for block in ("ROBOT_MIC_I2S_BCLK", "ROBOT_MIC_I2S_WS", "ROBOT_MIC_I2S_DIN"):
        match = re.search(
            rf"config {block}\n.*?default (-?\d+)", kconfig, re.S
        )
        assert match, f"{block} not found"
        assert match.group(1) == "-1", f"{block} must default to unset"


def test_kconfig_child_profile_defaults_to_empty():
    kconfig = _read(MAIN_DIR / "Kconfig.projbuild")
    match = re.search(
        r'config ROBOT_CHILD_PROFILE_ID\n.*?default "(.*?)"', kconfig, re.S
    )
    assert match, "ROBOT_CHILD_PROFILE_ID not found"
    assert match.group(1) == "", "profile id must default to empty"


def test_kconfig_language_hint_defaults_to_en():
    kconfig = _read(MAIN_DIR / "Kconfig.projbuild")
    match = re.search(
        r'config ROBOT_LANGUAGE_HINT\n.*?default "(.*?)"', kconfig, re.S
    )
    assert match, "ROBOT_LANGUAGE_HINT not found"
    assert match.group(1) == "en"


def test_capture_bounds_match_frozen_constants():
    header = _read(MAIN_DIR / "audio_capture.h")
    assert "#define AUDIO_CAPTURE_SAMPLE_RATE_HZ 16000" in header
    assert "#define AUDIO_CAPTURE_BITS_PER_SAMPLE 16" in header
    assert "#define AUDIO_CAPTURE_CHANNELS 1" in header
    assert "#define AUDIO_CAPTURE_MAX_SECONDS 15" in header
    assert "#define AUDIO_PCM_MAX_BYTES" in header
    assert (
        "#define AUDIO_WAV_FILE_MAX_BYTES "
        "(AUDIO_WAV_HEADER_BYTES + AUDIO_PCM_MAX_BYTES)"
    ) in header
    assert 16000 * 2 * 15 == 480000
    assert 44 + 480000 == 480044


def test_wav_header_is_riff_pcm16le_mono_16k():
    source = _read(MAIN_DIR / "audio_capture.c")
    for marker in ('"RIFF"', '"WAVE"', '"fmt "', '"data"'):
        assert marker in source, marker
    assert "write_u16le(hdr44 + 20, 1)" in source, "PCM format tag missing"
    assert "AUDIO_CAPTURE_SAMPLE_RATE_HZ" in source
    assert "I2S_SLOT_MODE_MONO" in source
    assert "I2S_DATA_BIT_WIDTH_16BIT" in source


def test_upload_targets_frozen_audio_endpoint():
    source = _read(MAIN_DIR / "backend_client.c")
    assert '"/api/v1/conversations/turn/audio"' in source


def test_multipart_headers_and_sidecar_match_contract():
    client = _read(MAIN_DIR / "backend_client.c")
    assert 'multipart/form-data; boundary=%s' in client
    assert '"Conversation-Sidecar"' in client
    assert '"Idempotency-Key"' in client
    assert '"Authorization"' in client
    assert '"Bearer %s"' in client, "device JWT must be sent as Bearer auth"
    assert "s_token" in client
    conversation = _read(MAIN_DIR / "conversation.c")
    for part in (
        'name=\\"audio\\"',
        'filename=\\"clip.wav\\"',
        "Content-Type: audio/wav",
        "CONV_MULTIPART_BOUNDARY",
    ):
        assert part in conversation, part


def test_sidecar_carries_required_child_profile_field():
    source = _read(MAIN_DIR / "conversation.c")
    assert '"{\\"child_profile_id\\":\\"%s\\"' in source or (
        '\\"child_profile_id\\":\\"%s\\"' in source
    ), "sidecar must carry child_profile_id"
    assert '\\"language_hint\\":\\"%s\\"' in source


def test_idempotency_key_generated_once_per_turn_and_reused():
    source = _read(MAIN_DIR / "conversation.c")
    assert source.count("identity_generate_uuid_v4") == 1, (
        "exactly one key generation per logical turn"
    )
    upload_section = source.split("static void run_upload", 1)[1].split(
        "static void run_turn", 1
    )[0]
    assert "identity_generate_uuid_v4" not in upload_section, (
        "the retry loop must never mint a new key for the same turn"
    )
    assert "backend_upload_audio_turn" in upload_section
    assert "idem_key" in upload_section


def test_retry_policy_covers_status_matrix():
    source = _read(MAIN_DIR / "conversation.c")
    for code in ("400", "401", "404", "409", "429", "503", "200"):
        assert f"meta.status_code == {code}" in source, code
    assert "backend_refresh_token" in source, "401 must refresh once"
    assert "reauthenticated" in source
    assert "#define CONV_UPLOAD_MAX_ATTEMPTS 3" in source
    assert "#define CONV_UPLOAD_DEADLINE_MS" in source
    assert "retry_after_s" in source
    assert "#define CONV_RETRY_AFTER_CAP_S 30" in source


def test_response_validation_accepts_null_audio():
    source = _read(MAIN_DIR / "conversation.c")
    assert "cJSON_IsNull(audio)" in source, "audio:null must be accepted"
    for field in (
        "conversation_id",
        "text",
        "language",
        "emotion",
        "action",
        "safety",
        "audio",
        "memory_writes",
    ):
        assert f'"{field}"' in source, field


def test_capture_stops_on_release_and_timeout():
    source = _read(MAIN_DIR / "conversation.c")
    assert "ptt_pressed()" in source
    assert "AUDIO_CAPTURE_MAX_SECONDS" in source


def test_capture_buffer_is_psram_allocated_with_checks():
    source = _read(MAIN_DIR / "conversation.c")
    assert "heap_caps_malloc(needed, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)" in source
    assert "s_body_buf == NULL" in source
    assert "telemetry_publish_audio_unavailable(\"buffer_alloc\")" in source


def test_fail_safe_on_capture_init_errors():
    source = _read(MAIN_DIR / "conversation.c")
    assert 'telemetry_publish_audio_capture_failed("i2s_init")' in source
    assert '"i2s_read"' in source, "read-loop failure must carry i2s_read reason"
    assert "capture_fail" in source
    assert "audio_capture_deinit()" in source
    capture = _read(MAIN_DIR / "audio_capture.c")
    assert "i2s_del_channel" in capture
    assert "ESP_ERR_INVALID_STATE" in capture


def test_audio_events_are_published_with_safe_payloads():
    telemetry = _read(MAIN_DIR / "telemetry.c")
    for event in (
        "audio.capture_started",
        "audio.capture_completed",
        "audio.capture_failed",
        "audio.upload_succeeded",
        "audio.upload_failed",
        "audio.unavailable",
    ):
        assert f'"{event}"' in telemetry, event
    conversation = _read(MAIN_DIR / "conversation.c")
    for publisher in (
        "telemetry_publish_audio_capture_started",
        "telemetry_publish_audio_capture_completed",
        "telemetry_publish_audio_capture_failed",
        "telemetry_publish_audio_upload_succeeded",
        "telemetry_publish_audio_upload_failed",
        "telemetry_publish_audio_unavailable",
    ):
        assert publisher in conversation, publisher


def test_conversation_never_logs_protected_values():
    """PTT MVP addition on top of the credential scanner: child profile
    ids, idempotency keys, and audio material must never reach a log
    statement. Same statement-level scanner as the credential test."""
    for path in _c_sources():
        for number, statement in _log_statements(path):
            lowered = statement.lower()
            for word in PROTECTED_LOG_WORDS:
                assert word not in lowered, (
                    f"{path.name}:{number} log statement contains "
                    f"protected word '{word}': {' '.join(statement.split())}"
                )


def test_upload_enforces_bounds_before_send():
    client = _read(MAIN_DIR / "backend_client.c")
    assert "body_len > CONV_UPLOAD_MAX_BODY_BYTES" in client
    assert "#define CONV_UPLOAD_MAX_BODY_BYTES (1024 * 1024 + 16 * 1024)" in client
    conversation = _read(MAIN_DIR / "conversation.c")
    assert "file_len > CONV_UPLOAD_MAX_FILE_BYTES" in conversation
    assert "total > CONV_UPLOAD_MAX_BODY_BYTES" in conversation


def test_app_main_initializes_conversation_subsystem():
    source = _read(MAIN_DIR / "app_main.c")
    assert "conversation_init()" in source
    assert '#include "conversation.h"' in source


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

"""Source-contract tests for the Compose-managed MQTT bridge (AGENTS.md
decision record 2026-10-15).

These tests read the repository's own infrastructure files as text (the
same pattern as the local-UI compose contract tests): they need no Docker
daemon and pin the approved Option A shape - a dedicated `bridge` service
on the Django image running `manage.py run_mqtt_bridge`, started only on
actually-healthy mongo/mosquitto, with no host ports and no new secrets.
"""

import re
from pathlib import Path

_SERVICE_RE = re.compile(r"^  ([a-z][a-z0-9_-]*):\s*$", re.MULTILINE)
_ENV_VAR_RE = re.compile(r"\$\{([A-Z0-9_]+)")


def _repo_root():
    return Path(__file__).resolve().parents[3]


def _compose_text():
    return (_repo_root() / "infra" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )


def _service_block(name, text=None):
    """Return the compose block of one 2-space-indented service, ending at
    the next service or the first top-level key (e.g. `volumes:`)."""
    text = _compose_text() if text is None else text
    matches = list(_SERVICE_RE.finditer(text))
    for index, match in enumerate(matches):
        if match.group(1) != name:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.start() : end]
        top_level = re.search(r"^[a-z]", block[1:], re.MULTILINE)
        if top_level:
            block = block[: top_level.start() + 1]
        return block
    return None


# --- The approved Option A shape -------------------------------------------


def test_compose_defines_a_dedicated_bridge_service():
    assert _service_block("bridge") is not None


def test_bridge_command_is_run_mqtt_bridge():
    bridge = _service_block("bridge")
    assert (
        'command: ["python", "manage.py", "run_mqtt_bridge"]' in bridge
    ), "the bridge service must run the existing management command only"


def test_bridge_uses_the_same_build_context_as_django():
    bridge = _service_block("bridge")
    django = _service_block("django")
    assert "build: ../backend" in bridge
    assert "build: ../backend" in django
    # Same image definition, different process: the web command must never
    # double as a bridge and vice versa.
    assert "run_mqtt_bridge" not in django


def test_bridge_starts_only_on_healthy_mongo_and_mosquitto():
    bridge = _service_block("bridge")
    assert "depends_on:" in bridge
    assert re.search(
        r"mongo:\s*\n\s+condition: service_healthy", bridge
    ), "bridge must wait for mongo's real healthcheck, not container start"
    assert re.search(
        r"mosquitto:\s*\n\s+condition: service_healthy", bridge
    ), "bridge must wait for mosquitto's real healthcheck, not container start"


def test_dependency_healthchecks_are_intact():
    # The healthy conditions above are only honest if the healthchecks
    # themselves still probe the services (not merely process start).
    assert "mongosh --quiet --file /healthcheck.js" in _service_block("mongo")
    assert "dynsec listRoles" in _service_block("mosquitto")
    assert "urllib.request.urlopen" in _service_block("ai-service")


def test_bridge_publishes_no_host_ports():
    assert "ports:" not in _service_block("bridge")


def test_bridge_has_no_volume_or_network_overrides():
    bridge = _service_block("bridge")
    assert "volumes:" not in bridge
    assert "networks:" not in bridge
    assert "container_name:" not in bridge


# --- Configuration reuse: same endpoints, same secret sources --------------


def test_bridge_mqtt_endpoint_matches_django_service():
    bridge = _service_block("bridge")
    django = _service_block("django")
    for line in (
        'MQTT_HOST: "mosquitto"',
        'MQTT_PORT: "1883"',
        'MQTT_BRIDGE_USERNAME: "django-service"',
        'MQTT_ADMIN_USERNAME: "admin"',
        'MONGO_HOST: "mongodb://mongo:27017/?replicaSet=rs0"',
        'MONGO_DB_NAME: "ai_talking_toy"',
    ):
        assert line in bridge, line
        assert line in django, line


def test_bridge_secrets_interpolate_env_never_literal():
    bridge = _service_block("bridge")
    assert "${DJANGO_SECRET_KEY:?DJANGO_SECRET_KEY must be set in .env}" in bridge
    assert "${MQTT_BRIDGE_PASSWORD:?MQTT_BRIDGE_PASSWORD must be set in .env}" in bridge
    assert "${MQTT_ADMIN_PASSWORD:?MQTT_ADMIN_PASSWORD must be set in .env}" in bridge
    # No quoted literal may ever stand in for a secret value.
    assert not re.search(r'(PASSWORD|SECRET_KEY):\s*"[^$]', bridge)


def test_bridge_references_no_env_key_absent_from_django():
    bridge_vars = set(_ENV_VAR_RE.findall(_service_block("bridge")))
    django_vars = set(_ENV_VAR_RE.findall(_service_block("django")))
    assert bridge_vars, "bridge must source configuration from .env keys"
    assert bridge_vars <= django_vars, (
        "bridge may only reuse .env keys the django service already uses: "
        f"{sorted(bridge_vars - django_vars)}"
    )


def test_bridge_env_stays_minimal():
    bridge = _service_block("bridge")
    for prefix in ("AI_SERVICE_", "LOCAL_UI_", "DEVICE_JWT_", "DEVICE_CHALLENGE"):
        assert prefix not in bridge, f"bridge must not carry {prefix}* settings"


# --- Launcher output stays honest ------------------------------------------


def _bat(name):
    # read_bytes: Path.read_text() would translate CRLF->LF via universal
    # newlines and defeat the line-ending guard below.
    return (_repo_root() / name).read_bytes().decode("utf-8")


def test_launcher_requires_bridge_container_running():
    bat = _bat("start-local.bat")
    # Readiness loop: bridge participates (state must be "running"), and
    # the status printer names it explicitly.
    assert "call :health_of bridge" in bat
    assert 'if /i not "%_H%"=="running" exit /b 1' in bat
    assert "bridge: %_H%" in bat


def test_launcher_and_stop_mention_bridge_and_stay_crlf():
    for name in ("start-local.bat", "stop-local.bat"):
        text = _bat(name)
        assert "bridge" in text, name
        # Regression guard for the bare-LF launcher defect: batch `goto`
        # falls through when the file on disk has LF-only line endings.
        assert "\r\n" in text, name
        assert re.search(r"(?<!\r)\n", text) is None, f"{name} has bare LF endings"

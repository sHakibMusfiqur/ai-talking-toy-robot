import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "infra" / "docker-compose.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"


def _service_block(service: str) -> str:
    text = COMPOSE.read_text(encoding="utf-8")
    match = re.search(
        rf"^  {re.escape(service)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:|\Z)",
        text,
        re.S | re.M,
    )
    assert match, f"service {service} not found in docker-compose.yml"
    return match.group(1)


def test_ai_service_exists_in_compose():
    block = _service_block("ai-service")
    assert "Dockerfile" in block


def test_ai_service_has_no_public_host_port():
    block = _service_block("ai-service")
    assert "ports:" not in block, "AI service must not be exposed on a host port"


def test_ai_service_requires_internal_token_from_env():
    block = _service_block("ai-service")
    assert "AI_SERVICE_INTERNAL_TOKEN: ${AI_SERVICE_INTERNAL_TOKEN:?" in block
    assert "MOSQUITTO" not in block
    assert "MQTT_" not in block


def test_existing_services_still_present():
    text = COMPOSE.read_text(encoding="utf-8")
    for service in ("mongo:", "mosquitto:", "django:", "ai-service:"):
        assert f"  {service}" in text


def test_env_example_documents_ai_service_token_placeholder():
    content = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "AI_SERVICE_INTERNAL_TOKEN=" in content
    line = next(
        line for line in content.splitlines() if line.startswith("AI_SERVICE_INTERNAL_TOKEN=")
    )
    value = line.split("=", 1)[1]
    assert "change-me" in value
    assert not re.fullmatch(r"[0-9a-f]{32,}", value), "env example must not carry a real secret"


def test_ai_service_source_has_no_hardcoded_secrets():
    root = Path(__file__).resolve().parents[1]
    secret_literal = re.compile(
        r"(TOKEN|SECRET|PASSWORD|API_KEY)\s*=\s*[\"'][0-9a-fA-F]{32,}[\"']"
    )
    private_key = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
    offenders = []
    for path in list(root.rglob("*.py")) + list(root.rglob("*.txt")) + list(root.rglob("Dockerfile")):
        if "tests" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if secret_literal.search(source) or private_key.search(source):
            offenders.append(path.name)
    assert offenders == []

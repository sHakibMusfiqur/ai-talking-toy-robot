import re
from pathlib import Path

from django.conf import settings

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "infra" / "mosquitto" / "docker-entrypoint.sh"
MOSQUITTO_CONF = REPO_ROOT / "infra" / "mosquitto" / "mosquitto.conf"

# `$CTRL dynsec addRoleACL <role> <acltype> <topic> allow <priority> ...`
ACL_LINE = re.compile(
    r"addRoleACL\s+(?P<role>\S+)\s+(?P<acltype>\S+)\s+"
    r'"(?P<topic>[^"]+)"\s+(?P<effect>\S+)'
)


def _acls(role, acl_type=None):
    entry = ENTRYPOINT.read_text(encoding="utf-8")
    return {
        m.group("topic")
        for m in ACL_LINE.finditer(entry)
        if m.group("role") == role
        and (acl_type is None or m.group("acltype") == acl_type)
        and m.group("effect") == "allow"
    }


def test_mosquitto_conf_enforces_authentication_and_dynsec():
    conf = MOSQUITTO_CONF.read_text(encoding="utf-8")
    assert "allow_anonymous false" in conf
    assert "mosquitto_dynamic_security.so" in conf
    assert "plugin_opt_config_file" in conf


def test_entrypoint_denies_receive_by_default():
    entry = ENTRYPOINT.read_text(encoding="utf-8")
    assert re.search(r"setDefaultACLAccess\s+publishClientReceive\s+deny", entry)
    # No allow-by-default overrides for publish/send or subscribe.
    assert not re.search(r"setDefaultACLAccess\s+(publishClientSend|subscribe)\s+allow", entry)


def test_device_role_is_scoped_to_own_topics_only():
    assert _acls("device", "publishClientSend") == {
        "robots/%u/status",
        "robots/%u/heartbeat",
        "robots/%u/event",
    }
    own_inbound = {
        "robots/%u/command",
        "robots/%u/config/notify",
        "robots/%u/ota/notify",
        "robots/%u/content/notify",
    }
    assert _acls("device", "subscribePattern") == own_inbound
    assert _acls("device", "publishClientReceive") == own_inbound


def test_django_service_rights_match_contract_and_never_wildcard_publish():
    assert _acls("django-service", "publishClientSend") == {
        "robots/+/command",
        "robots/+/config/notify",
        "robots/+/ota/notify",
        "robots/+/content/notify",
    }
    backend_inbound = {
        "robots/+/status",
        "robots/+/heartbeat",
        "robots/+/event",
    }
    assert _acls("django-service", "subscribePattern") == backend_inbound
    assert _acls("django-service", "publishClientReceive") == backend_inbound


def test_no_role_has_wildcard_publish_rights():
    # Section 11.2: no client ever gets wildcard '#' publish rights.
    entry = ENTRYPOINT.read_text(encoding="utf-8")
    for m in ACL_LINE.finditer(entry):
        if m.group("acltype") == "publishClientSend":
            assert "#" not in m.group("topic")


def test_no_role_grants_publish_rights_to_device_status_topics():
    # Django must never publish fake status/event (Section 11.2).
    for topic in _acls("django-service", "publishClientSend"):
        category = topic.split("/")[2]
        assert category not in {"status", "heartbeat", "event"}


def test_bridge_settings_default_to_service_credentials():
    assert settings.MQTT_BRIDGE_USERNAME == "django-service"
    assert settings.MQTT_ADMIN_USERNAME == "admin"
    assert settings.MQTT_RECONNECT_MIN_DELAY_SECONDS >= 1
    assert (
        settings.MQTT_RECONNECT_MAX_DELAY_SECONDS
        >= settings.MQTT_RECONNECT_MIN_DELAY_SECONDS
    )

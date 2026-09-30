import json
import logging
import threading
import uuid

from django.conf import settings

from .client import MqttConnection, MqttError, MqttRejected

logger = logging.getLogger(__name__)

CONTROL_TOPIC = "$CONTROL/dynamic-security/v1"
RESPONSE_TOPIC = "$CONTROL/dynamic-security/v1/response"

DUPLICATE_CLIENT_ERROR = "Client already exists"
DUPLICATE_ROLE_ERROR = "Internal error"

DEVICE_ROLE = "device"
BRIDGE_ROLE = "django-service"


class _DynsecError(MqttError):
    def __init__(self, error):
        super().__init__(f"dynsec error: {error}")
        self.error = error


def _run_commands(commands, timeout=5.0):
    """Send one dynsec request batch and wait for its response. Returns the
    response dicts; raises MqttError on transport failure or timeout and
    _DynsecError on a command-level error (duplicate handling stays with
    callers, since 'already exists' is a success condition there)."""
    if isinstance(commands, dict):
        commands = [commands]
    if not settings.MQTT_ADMIN_PASSWORD:
        raise MqttError("mqtt admin password not configured")
    received = []
    done = threading.Event()
    subscribed = threading.Event()

    def on_message(client, userdata, message):
        try:
            received.append(json.loads(message.payload))
        except ValueError:
            return
        done.set()

    def on_subscribe(client, userdata, mid, reason_codes, properties):
        subscribed.set()

    conn = MqttConnection(
        client_id=f"dynsec-{uuid.uuid4().hex[:12]}",
        username=settings.MQTT_ADMIN_USERNAME,
        password=settings.MQTT_ADMIN_PASSWORD,
        on_message=on_message,
    )
    conn._client.on_subscribe = on_subscribe
    conn.connect(timeout=timeout)
    try:
        conn.subscribe([(RESPONSE_TOPIC, 1)])
        if not subscribed.wait(timeout):
            raise MqttError("dynsec response subscription not confirmed")
        conn.publish(
            CONTROL_TOPIC,
            json.dumps({"commands": commands}),
            qos=1,
            timeout=timeout,
        )
        if not done.wait(timeout):
            raise MqttError("dynsec response timeout")
    finally:
        conn.disconnect()

    responses = received[0].get("responses") if received else None
    if not isinstance(responses, list) or len(responses) != len(commands):
        raise MqttError("dynsec response did not match request")
    return responses


def _single(commands, timeout=5.0):
    """Run a single command; return its response dict (error included)."""
    return _run_commands(commands, timeout=timeout)[0]


def _ensure_user(username, password, role):
    if not username or not password:
        raise MqttError("mqtt client credentials missing")
    response = _single(
        {"command": "createClient", "username": username, "password": password}
    )
    if response.get("error") == DUPLICATE_CLIENT_ERROR:
        response = _single(
            {"command": "setClientPassword", "username": username, "password": password}
        )
    elif "error" in response:
        raise _DynsecError(response["error"])
    if response.get("error"):
        raise _DynsecError(response["error"])

    response = _single(
        {
            "command": "addClientRole",
            "username": username,
            "rolename": role,
            "priority": 1,
        }
    )
    if "error" in response and response["error"] != DUPLICATE_ROLE_ERROR:
        raise _DynsecError(response["error"])


def ensure_device_client(device_id, token):
    """Provision (or rotate) the broker client for one device: username =
    device_id, password = the freshly issued JWT, role = device (Section 11.2
    per-device ACL). Called best-effort from token exchange; any failure
    raises MqttError so the caller can log-and-continue."""
    _ensure_user(device_id, token, DEVICE_ROLE)
    logger.info(
        "mqtt device client provisioned",
        extra={"device_id": device_id},
    )


def ensure_bridge_client():
    """Provision (or rotate) the django-service broker client."""
    _ensure_user(
        settings.MQTT_BRIDGE_USERNAME,
        settings.MQTT_BRIDGE_PASSWORD,
        BRIDGE_ROLE,
    )
    logger.info(
        "mqtt bridge client provisioned",
        extra={"mqtt_client_id": settings.MQTT_BRIDGE_USERNAME},
    )

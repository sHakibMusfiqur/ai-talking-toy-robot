import json

from rest_framework import serializers

MAX_PAYLOAD_BYTES = 8192

# MQTT_PUBLISH_FIELDS is intentionally explicit: firmware cannot smuggle
# extra keys past validation (topic/device mismatch via payload is also
# impossible — identity always comes from the topic, never the payload).
ALLOWED_STATUS_FIELDS = ("online", "state", "battery_percent", "rssi")
ALLOWED_EVENT_FIELDS = ("event_type", "payload")


def parse_inbound_payload(raw):
    """Decode bytes/str broker payload to a dict; raise ValueError if not a
    JSON object, invalid UTF-8, or larger than MAX_PAYLOAD_BYTES."""
    if raw is None:
        raw = b""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise ValueError("payload too large")
    if not raw:
        raise ValueError("empty payload")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError("payload is not valid JSON")
    if not isinstance(data, dict):
        raise ValueError("payload must be a JSON object")
    return data


def _reject_unknown(data, allowed):
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise serializers.ValidationError(
            f"unknown field(s): {', '.join(unknown)}"
        )


class StatusPayloadSerializer(serializers.Serializer):
    """Partial-update semantics: only fields present in the payload change;
    omitted fields keep their previous value (single write path with REST).
    JSON types are enforced strictly (a string "yes" is not a boolean) —
    coercion would silently mask firmware payload bugs."""

    online = serializers.BooleanField(required=False)
    state = serializers.CharField(required=False, max_length=32, allow_blank=True)
    battery_percent = serializers.IntegerField(
        required=False, min_value=0, max_value=100
    )
    rssi = serializers.IntegerField(required=False, min_value=-300, max_value=300)

    def validate(self, attrs):
        raw = self.initial_data
        _reject_unknown(raw, ALLOWED_STATUS_FIELDS)
        if not raw:
            raise serializers.ValidationError("payload has no status fields")
        if "online" in raw and not isinstance(raw["online"], bool):
            raise serializers.ValidationError({"online": "Must be a boolean."})
        if "state" in raw and not isinstance(raw["state"], str):
            raise serializers.ValidationError({"state": "Must be a string."})
        for int_field in ("battery_percent", "rssi"):
            if int_field in raw and (
                isinstance(raw[int_field], bool)
                or not isinstance(raw[int_field], int)
            ):
                raise serializers.ValidationError(
                    {int_field: "Must be an integer."}
                )
        return attrs


class HeartbeatPayloadSerializer(serializers.Serializer):
    """Section 11 defines no heartbeat payload fields. Empty object only —
    any field is rejected until the frozen contract adds one."""

    def validate(self, attrs):
        _reject_unknown(self.initial_data, ())
        return {}


class EventPayloadSerializer(serializers.Serializer):
    event_type = serializers.RegexField(
        regex=r"^[A-Za-z0-9_.\-]{1,64}$",
        error_messages={
            "invalid": (
                "event_type must be 1-64 chars of letters, digits, '_', '.' or '-'."
            )
        },
    )
    payload = serializers.JSONField(required=False)

    def validate(self, attrs):
        _reject_unknown(self.initial_data, ALLOWED_EVENT_FIELDS)
        payload = attrs.get("payload", {})
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise serializers.ValidationError(
                {"payload": "payload must be a JSON object."}
            )
        # Bounded event payload — events are diagnostics, not data transport.
        if len(json.dumps(payload, default=str)) > MAX_PAYLOAD_BYTES // 2:
            raise serializers.ValidationError(
                {"payload": "payload is too large."}
            )
        attrs["payload"] = payload
        return attrs


class DeviceCommandSerializer(serializers.Serializer):
    """Phase 8 command envelope: a mechanical action name only (Phase 13
    defines semantics; no execution happens in this phase)."""

    action = serializers.RegexField(
        regex=r"^[A-Za-z0-9_.\-]{1,64}$",
        error_messages={
            "invalid": "action must be 1-64 chars of letters, digits, '_', '.' or '-'."
        },
    )

    def validate(self, attrs):
        _reject_unknown(self.initial_data, ("action",))
        if "action" in self.initial_data and not isinstance(
            self.initial_data["action"], str
        ):
            raise serializers.ValidationError({"action": "Must be a string."})
        return attrs


class NotifyPayloadSerializer(serializers.Serializer):
    """Frozen notify shape: a version marker only (Section 11.2)."""

    version = serializers.IntegerField(min_value=0)


def serialize_notify_payload(version):
    NotifyPayloadSerializer(data={"version": version}).is_valid(raise_exception=True)
    return json.dumps({"version": version})

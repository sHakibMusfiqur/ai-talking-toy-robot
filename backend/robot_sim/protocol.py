"""Frozen device-side protocol logic, shared by the simulator runtime and
unit tests (no I/O, no Django settings access).

Mirrors the firmware contract exactly:
- topics/QoS/retain come from the ONE existing definition (mqtt_bridge.topics)
- the action whitelist is the ONE existing definition (core.semantic_actions)
- command parsing mirrors firmware actions.c process_command():
  invalid JSON / non-object / missing-or-non-string action ->
  ``invalid_payload`` with an empty action string; case-insensitive match
  with 1..64 char bound -> canonical UPPERCASE action; anything else ->
  ``unknown_action`` with the raw value verbatim (truncated to 64 chars,
  as the firmware's ``%.64s`` does).
The simulator additionally stamps ``"simulated": true`` into command event
payloads so simulated activity is never mistaken for real hardware.
"""

import json
import re

from core.semantic_actions import SEMANTIC_ACTIONS
from mqtt_bridge import topics

# Firmware actions.c: ACTION_MAX 65 (incl. NUL) -> accepted length 1..64.
MAX_ACTION_LEN = 64

STATE_BOOT = "BOOT"
STATE_IDLE = "IDLE"

SIMULATED_FLAG = "simulated"

# Event type rule kept identical to the backend's EventPayloadSerializer.
EVENT_TYPE_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")

# Section 11.2: heartbeat carries no fields at all.
HEARTBEAT_PAYLOAD = "{}"

# LWT and graceful-offline use the same partial status shape.
OFFLINE_STATUS_JSON = '{"online":false}'


def own_subscriptions(device_id):
    """The exact four filters the firmware subscribes to (own command +
    the three notify markers), QoS 1, no wildcards, own id only."""
    subs = [(topics.command_topic(device_id), topics.QOS_COMMAND)]
    for kind in topics.NOTIFY_KINDS:
        subs.append((topics.notify_topic(device_id, kind), topics.QOS_NOTIFY))
    return tuple(subs)


def match_semantic_action(value):
    """Case-insensitive membership against the frozen set; returns the
    canonical UPPERCASE action or None (firmware match_semantic_action)."""
    if not isinstance(value, str):
        return None
    if len(value) == 0 or len(value) > MAX_ACTION_LEN:
        return None
    upper = value.upper()
    return upper if upper in SEMANTIC_ACTIONS else None


def command_event(event_type, action="", reason=None):
    """Command/lifecycle event envelope. ``simulated`` marks every event
    this package emits; ``action`` follows the firmware payload shape
    (always a string, empty when unknown)."""
    payload = {"action": action, SIMULATED_FLAG: True}
    if reason is not None:
        payload["reason"] = reason
    return {"event_type": event_type, "payload": payload}


def lifecycle_event(event_type, payload=None):
    body = {SIMULATED_FLAG: True}
    if payload:
        body.update(payload)
    return {"event_type": event_type, "payload": body}


def status_json(state, online=True):
    """Partial status update: only online/state - the simulator has no
    radio or battery, so it must never fabricate rssi/battery_percent."""
    return json.dumps(
        {"online": bool(online), "state": state}, separators=(",", ":")
    )


def evaluate_command(raw):
    """Parse one raw command payload. Returns ``(action, reject_event)``:
    exactly one of the two is set. ``action`` is the canonical uppercase
    action to execute; ``reject_event`` is the ready-to-publish
    ``command.rejected`` event (firmware process_command semantics)."""
    action_value = None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, dict):
        value = data.get("action")
        if isinstance(value, str):
            action_value = value

    if action_value is None:
        # Firmware: unparseable payload or action not a string -> reject
        # with reason invalid_payload and an empty action string.
        return None, command_event(
            "command.rejected", action="", reason="invalid_payload"
        )

    canonical = match_semantic_action(action_value)
    if canonical is None:
        # Firmware %.64s: raw value verbatim, truncated to 64 chars.
        return None, command_event(
            "command.rejected",
            action=action_value[:MAX_ACTION_LEN],
            reason="unknown_action",
        )
    return canonical, None

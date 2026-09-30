PREFIX = "robots"

STATUS = "status"
HEARTBEAT = "heartbeat"
EVENT = "event"
COMMAND = "command"
NOTIFY_KINDS = ("config", "ota", "content")

DEVICE_PUBLISH_CATEGORIES = frozenset({STATUS, HEARTBEAT, EVENT})
BACKEND_PUBLISH_CATEGORIES = frozenset({COMMAND})
BACKEND_PUBLISH_NOTIFY = frozenset(NOTIFY_KINDS)

# Section 11.2: QoS 1 for everything except heartbeat (QoS 0).
QOS_STATUS = 1
QOS_HEARTBEAT = 0
QOS_EVENT = 1
QOS_COMMAND = 1
QOS_NOTIFY = 1

# Section 11.2: status + notify markers retained; command/heartbeat/event not.
RETAINED_STATUS = True
RETAINED_HEARTBEAT = False
RETAINED_EVENT = False
RETAINED_COMMAND = False
RETAINED_NOTIFY = True

# Exact filters the django-service client subscribes to (Section 11.2).
BACKEND_SUBSCRIPTIONS = (
    ("robots/+/status", QOS_STATUS),
    ("robots/+/heartbeat", QOS_HEARTBEAT),
    ("robots/+/event", QOS_EVENT),
)


FORBIDDEN_ID_CHARS = ("#", "+", "/")


def _valid_device_id(device_id):
    return bool(device_id) and not any(c in device_id for c in FORBIDDEN_ID_CHARS)


def device_topic(device_id, category):
    if not _valid_device_id(device_id):
        raise ValueError(f"invalid device id for topic: {device_id!r}")
    return f"{PREFIX}/{device_id}/{category}"


def status_topic(device_id):
    return device_topic(device_id, STATUS)


def heartbeat_topic(device_id):
    return device_topic(device_id, HEARTBEAT)


def event_topic(device_id):
    return device_topic(device_id, EVENT)


def command_topic(device_id):
    return device_topic(device_id, COMMAND)


def notify_topic(device_id, kind):
    if kind not in NOTIFY_KINDS:
        raise ValueError(f"unknown notify kind: {kind!r}")
    if not _valid_device_id(device_id):
        raise ValueError(f"invalid device id for topic: {device_id!r}")
    return f"{PREFIX}/{device_id}/{kind}/notify"


def parse_topic(topic):
    """Return (device_id, category, notify_kind) for a well-formed robot
    topic, else None. Only the Section 11.1 shapes parse — anything else
    (including wildcards or extra levels) is rejected before touching code."""
    if not isinstance(topic, str):
        return None
    parts = topic.split("/")
    if len(parts) == 3:
        prefix, device_id, category = parts
        if prefix != PREFIX or not _valid_device_id(device_id):
            return None
        if category in DEVICE_PUBLISH_CATEGORIES or category == COMMAND:
            return device_id, category, None
        return None
    if len(parts) == 4:
        prefix, device_id, kind, tail = parts
        if prefix != PREFIX or not _valid_device_id(device_id):
            return None
        if kind in NOTIFY_KINDS and tail == "notify":
            return device_id, kind, "notify"
        return None
    return None

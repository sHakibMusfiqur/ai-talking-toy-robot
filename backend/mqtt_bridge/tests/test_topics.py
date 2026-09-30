import pytest

from mqtt_bridge import topics


def test_qos_matches_frozen_contract():
    # Section 11.1/11.2: QoS 1 everywhere except heartbeat (QoS 0).
    assert topics.QOS_STATUS == 1
    assert topics.QOS_HEARTBEAT == 0
    assert topics.QOS_EVENT == 1
    assert topics.QOS_COMMAND == 1
    assert topics.QOS_NOTIFY == 1


def test_retained_flags_match_frozen_contract():
    # Section 11.2: status + notify markers retained; heartbeat, event and
    # command are momentary (not retained).
    assert topics.RETAINED_STATUS is True
    assert topics.RETAINED_HEARTBEAT is False
    assert topics.RETAINED_EVENT is False
    assert topics.RETAINED_COMMAND is False
    assert topics.RETAINED_NOTIFY is True


def test_backend_subscriptions_are_exact_contract_filters():
    assert topics.BACKEND_SUBSCRIPTIONS == (
        ("robots/+/status", 1),
        ("robots/+/heartbeat", 0),
        ("robots/+/event", 1),
    )


def test_topic_builders_produce_exact_contract_shapes():
    d = "11111111-2222-4333-8444-555555555555"
    assert topics.status_topic(d) == f"robots/{d}/status"
    assert topics.heartbeat_topic(d) == f"robots/{d}/heartbeat"
    assert topics.event_topic(d) == f"robots/{d}/event"
    assert topics.command_topic(d) == f"robots/{d}/command"
    assert topics.notify_topic(d, "config") == f"robots/{d}/config/notify"
    assert topics.notify_topic(d, "ota") == f"robots/{d}/ota/notify"
    assert topics.notify_topic(d, "content") == f"robots/{d}/content/notify"


def test_builders_reject_wildcards_and_slashes_in_device_id():
    for bad in ("#", "+", "a/b", "robots/+/status"):
        with pytest.raises(ValueError):
            topics.device_topic(bad, "status")


def test_unknown_notify_kind_rejected():
    with pytest.raises(ValueError):
        topics.notify_topic("d1", "firmware")


def test_parse_round_trip():
    d = "11111111-2222-4333-8444-555555555555"
    assert topics.parse_topic(topics.status_topic(d)) == (d, "status", None)
    assert topics.parse_topic(topics.heartbeat_topic(d)) == (d, "heartbeat", None)
    assert topics.parse_topic(topics.event_topic(d)) == (d, "event", None)
    assert topics.parse_topic(topics.command_topic(d)) == (d, "command", None)
    assert topics.parse_topic(topics.notify_topic(d, "ota")) == (d, "ota", "notify")


@pytest.mark.parametrize(
    "topic",
    [
        "",
        "robots",
        "robots/x",
        "robots/x/status/extra",
        "other/x/status",
        "robots//status",
        "robots/x/unknown",
        "robots/x/config/notify/extra",
        "robots/x/notify/config",
        "robots/+/status",
        "robots/#",
        "x/y/z",
    ],
)
def test_parse_rejects_malformed_topics(topic):
    assert topics.parse_topic(topic) is None

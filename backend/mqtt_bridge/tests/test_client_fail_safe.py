"""Phase A (F-08): a publish that fails on the transport must poison the
connection so the queued QoS 1 copy can never be delivered later by an
auto-reconnect. These tests exercise MqttConnection.publish against a fake
paho client — no broker needed."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.test import override_settings
from mqtt_bridge.client import MqttConnection, MqttError


def _connection_with_fake_client(fake_client):
    connection = MqttConnection.__new__(MqttConnection)
    connection.host = "localhost"
    connection.port = 1883
    connection.client_id = "test"
    connection._client = fake_client
    connection._subscriptions = []
    connection._connected = MagicMock()
    connection._connect_error = None
    connection._lock = MagicMock()
    connection._failed = False
    return connection


def _fake_paho_client(*, connected=True, rc=0, publish_raises=None,
                      wait_raises=None, is_published=True):
    client = MagicMock()
    client.is_connected.return_value = connected
    info = MagicMock()
    info.rc = rc
    if wait_raises is not None:
        info.wait_for_publish.side_effect = wait_raises
    info.is_published.return_value = is_published
    if publish_raises is not None:
        client.publish.side_effect = publish_raises
    else:
        client.publish.return_value = info
    return client, info


@override_settings(MQTT_PUBLISH_TIMEOUT_SECONDS=1)
def test_publish_fails_fast_when_not_connected():
    client, info = _fake_paho_client(connected=False)
    connection = _connection_with_fake_client(client)
    with pytest.raises(MqttError, match="not connected"):
        connection.publish("t", "{}", qos=1)
    # Fail-fast: paho.publish was never called, so nothing was enqueued.
    client.publish.assert_not_called()
    assert connection.is_usable() is False


@override_settings(MQTT_PUBLISH_TIMEOUT_SECONDS=1)
def test_publish_rejected_by_paho_poisons_connection():
    # paho returned rc != SUCCESS — the message may already be queued in
    # _out_messages; the connection must be poisoned, torn down (network
    # loop stopped, so the queued copy can never be re-sent), and unusable.
    client, info = _fake_paho_client(rc=4)  # MQTT_ERR_NO_LOST ... any non-zero
    connection = _connection_with_fake_client(client)
    with pytest.raises(MqttError, match="not accepted"):
        connection.publish("t", "{}", qos=1)
    assert connection._failed is True
    assert connection.is_usable() is False
    # The network loop is stopped and the session closed: nothing can
    # transmit a queued QoS 1 copy later.
    client.disconnect.assert_called_once()
    client.loop_stop.assert_called_once()
    # A poisoned connection refuses further publishes outright.
    with pytest.raises(MqttError, match="poisoned"):
        connection.publish("t", "{}", qos=1)


@override_settings(MQTT_PUBLISH_TIMEOUT_SECONDS=1)
def test_publish_ack_timeout_poisons_connection():
    client, info = _fake_paho_client(
        wait_raises=RuntimeError("Timeout waiting for publish")
    )
    connection = _connection_with_fake_client(client)
    with pytest.raises(MqttError, match="not acknowledged"):
        connection.publish("t", "{}", qos=1)
    assert connection._failed is True
    assert connection.is_usable() is False
    client.loop_stop.assert_called_once()


@override_settings(MQTT_PUBLISH_TIMEOUT_SECONDS=1)
def test_publish_unpublished_result_poisons_connection():
    client, info = _fake_paho_client(is_published=False)
    connection = _connection_with_fake_client(client)
    with pytest.raises(MqttError, match="not acknowledged"):
        connection.publish("t", "{}", qos=1)
    assert connection._failed is True
    client.loop_stop.assert_called_once()


@override_settings(MQTT_PUBLISH_TIMEOUT_SECONDS=1)
def test_successful_publish_leaves_connection_usable():
    client, info = _fake_paho_client()
    connection = _connection_with_fake_client(client)
    connection.publish("t", "{}", qos=1)
    info.wait_for_publish.assert_called_once()
    assert connection._failed is False
    assert connection.is_usable() is True
    # A healthy publish must NOT tear anything down.
    client.disconnect.assert_not_called()
    client.loop_stop.assert_not_called()


def test_get_publisher_discards_poisoned_connection(monkeypatch):
    """get_publisher must never hand out a poisoned connection: it
    disconnects the old one and builds a fresh MqttConnection."""
    from mqtt_bridge import publisher as publisher_module

    poisoned = MagicMock()
    poisoned.is_usable.return_value = False

    fresh_connection = MagicMock()
    fresh_publisher = MagicMock()
    fresh_publisher.connection = fresh_connection

    created = []

    def _fake_mqtt_connection(**kwargs):
        created.append(kwargs)
        return fresh_connection

    monkeypatch.setattr(publisher_module, "MqttConnection", _fake_mqtt_connection)
    monkeypatch.setattr(
        publisher_module,
        "MqttPublisher",
        lambda connection: fresh_publisher,
    )

    old_publisher = MagicMock()
    old_publisher.connection = poisoned
    monkeypatch.setattr(publisher_module, "_publisher", old_publisher)

    pub = publisher_module.get_publisher()
    assert pub is fresh_publisher
    # The poisoned connection was torn down.
    poisoned.disconnect.assert_called_once()
    # Exactly one new connection was built.
    assert len(created) == 1

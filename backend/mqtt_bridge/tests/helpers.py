import json
import time
import uuid

import paho.mqtt.client as mqtt
from django.conf import settings


class ConnectionRefused(Exception):
    def __init__(self, reason):
        super().__init__(f"mqtt connection refused: {reason}")
        self.reason = str(reason)


def wait_until(predicate, timeout=5.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    return predicate()


class MqttTestClient:
    """paho v2 client with recording callbacks (5-arg VERSION2 signatures)
    and blocking helpers for assertions."""

    def __init__(self, *, username, password, client_id=None, will=None):
        self.username = username
        self.client_id = client_id or f"test-{uuid.uuid4().hex[:16]}"
        self.messages = []
        self._subacks = {}
        self._connect_reason = None
        self._connected = False
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.client_id,
            clean_session=True,
            protocol=mqtt.MQTTv311,
        )
        self._client.username_pw_set(username, password)
        self._client.on_connect = self._on_connect
        self._client.on_subscribe = self._on_subscribe
        self._client.on_message = self._on_message
        if will is not None:
            topic, payload, qos, retain = will
            self._client.will_set(topic, payload, qos=qos, retain=retain)

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        self._connect_reason = reason_code
        self._connected = not reason_code.is_failure

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties):
        self._subacks[mid] = [
            rc.value if hasattr(rc, "value") else int(rc) for rc in reason_codes
        ]

    def _on_message(self, client, userdata, message):
        self.messages.append(
            {
                "topic": message.topic,
                "payload": message.payload,
                "qos": message.qos,
                "retain": message.retain,
            }
        )

    def connect(self, timeout=5.0):
        try:
            self._client.connect(settings.MQTT_HOST, settings.MQTT_PORT, keepalive=30)
        except OSError as exc:
            raise ConnectionRefused(str(exc)) from exc
        self._client.loop_start()
        deadline = time.monotonic() + timeout
        while self._connect_reason is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if self._connect_reason is None:
            self.disconnect()
            raise ConnectionRefused("connect timeout")
        if not self._connected:
            reason = str(self._connect_reason)
            self.disconnect()
            raise ConnectionRefused(reason)
        return self

    def subscribe(self, topic, qos=1):
        result, mid = self._client.subscribe(topic, qos=qos)
        assert result == mqtt.MQTT_ERR_SUCCESS, result
        deadline = time.monotonic() + 5.0
        while mid not in self._subacks and time.monotonic() < deadline:
            time.sleep(0.02)
        assert mid in self._subacks, "no SUBACK received"
        return self._subacks[mid]

    def publish(self, topic, payload, qos=1, retain=False):
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload)
        info = self._client.publish(topic, payload, qos=qos, retain=retain)
        info.wait_for_publish(timeout=5.0)
        return info

    def received(self, topic=None):
        if topic is None:
            return list(self.messages)
        return [m for m in self.messages if m["topic"] == topic]

    def wait_received(self, topic, timeout=5.0):
        wait_until(lambda: bool(self.received(topic)), timeout=timeout)
        return self.received(topic)

    def clear(self):
        self.messages.clear()

    def simulate_network_drop(self):
        sock = getattr(self._client, "_sock", None)
        if sock is not None:
            sock.close()

    def disconnect(self):
        try:
            self._client.disconnect()
        except Exception:  # noqa: BLE001 - teardown must never raise
            pass
        try:
            self._client.loop_stop()
        except Exception:  # noqa: BLE001 - teardown must never raise
            pass
        self._connected = False

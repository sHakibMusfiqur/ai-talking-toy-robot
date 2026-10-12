import logging
import threading

import paho.mqtt.client as mqtt
from django.conf import settings

logger = logging.getLogger(__name__)


class MqttError(Exception):
    """Transport-level MQTT failure (unreachable broker, timeout, no ack)."""


class MqttRejected(MqttError):
    """Broker rejected the connection (bad credentials / ACL denial)."""


def backoff_delay(attempt, base=None, cap=None):
    """Deterministic exponential backoff, bounded by cap (unit-testable;
    no jitter — single bridge instance per deployment)."""
    base = base if base is not None else settings.MQTT_RECONNECT_MIN_DELAY_SECONDS
    cap = cap if cap is not None else settings.MQTT_RECONNECT_MAX_DELAY_SECONDS
    return max(base, min(cap, base * (2 ** max(0, int(attempt)))))


class MqttConnection:
    def __init__(
        self,
        *,
        client_id,
        username=None,
        password=None,
        will=None,
        on_message=None,
        keepalive=60,
    ):
        self.host = settings.MQTT_HOST
        self.port = settings.MQTT_PORT
        self.client_id = client_id
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=True,
            protocol=mqtt.MQTTv311,
        )
        if username is not None:
            self._client.username_pw_set(username, password or None)
        self._client.reconnect_delay_set(
            min_delay=settings.MQTT_RECONNECT_MIN_DELAY_SECONDS,
            max_delay=settings.MQTT_RECONNECT_MAX_DELAY_SECONDS,
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        if on_message is not None:
            self._client.on_message = on_message
        if will is not None:
            will_topic, will_payload, will_qos, will_retain = will
            self._client.will_set(
                will_topic, will_payload, qos=will_qos, retain=will_retain
            )
        self._subscriptions = []
        self._connected = threading.Event()
        self._connect_error = None
        self._lock = threading.Lock()
        self._failed = False

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            self._connect_error = str(reason_code)
            self._connected.set()
            logger.error(
                "mqtt connection rejected by broker",
                extra={"mqtt_client_id": self.client_id, "reason": str(reason_code)},
            )
            return
        with self._lock:
            for topic, qos in self._subscriptions:
                client.subscribe(topic, qos=qos)
        self._connect_error = None
        self._connected.set()
        logger.info(
            "mqtt connected", extra={"mqtt_client_id": self.client_id}
        )

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        self._connected.clear()
        logger.info(
            "mqtt disconnected",
            extra={
                "mqtt_client_id": self.client_id,
                "reason": str(reason_code),
            },
        )

    def subscribe(self, subscriptions):
        """Record exact (filter, qos) subscriptions; applied on every connect
        (including auto-reconnects, Section 11.2 reconnect behavior)."""
        with self._lock:
            self._subscriptions = list(subscriptions)
            if self._connected.is_set() and not self._connect_error:
                for topic, qos in self._subscriptions:
                    self._client.subscribe(topic, qos=qos)

    def connect(self, timeout=None):
        """Single bounded connect attempt. Raises MqttError on unreachable
        broker or timeout, MqttRejected when the broker refuses credentials."""
        timeout = timeout if timeout is not None else settings.MQTT_CONNECT_TIMEOUT_SECONDS
        self._connected.clear()
        self._connect_error = None
        try:
            self._client.connect(self.host, self.port, keepalive=30)
        except OSError as exc:
            raise MqttError(f"mqtt tcp connect failed: {exc}") from exc
        self._client.loop_start()
        if not self._connected.wait(timeout):
            self.disconnect()
            raise MqttError("mqtt connect timed out")
        if self._connect_error is not None:
            error = self._connect_error
            self.disconnect()
            raise MqttRejected(f"mqtt connection rejected: {error}")
        return self

    def publish(self, topic, payload, *, qos, retain=False, timeout=None):
        
        if self._failed:
            raise MqttError("mqtt connection poisoned after previous failure")
        if not self._client.is_connected():
            
            raise MqttError("mqtt not connected")
        timeout = timeout if timeout is not None else settings.MQTT_PUBLISH_TIMEOUT_SECONDS
        try:
            info = self._client.publish(topic, payload, qos=qos, retain=retain)
        except RuntimeError as exc:
            self._poison()
            raise MqttError(f"mqtt publish raised: {exc}") from exc
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            # paho may have enqueued the message despite the failure —
            # poison so the queued copy can never be re-sent.
            self._poison()
            raise MqttError(f"mqtt publish not accepted (rc={info.rc})")
        try:
            info.wait_for_publish(timeout=timeout)
        except RuntimeError as exc:
            self._poison()
            raise MqttError(f"mqtt publish not acknowledged: {exc}") from exc
        if not info.is_published():
            self._poison()
            raise MqttError("mqtt publish not acknowledged")

    def _poison(self):
        
        self._failed = True
        try:
            self._client.disconnect()
        except Exception:  # noqa: BLE001 - teardown must never raise
            pass
        try:
            self._client.loop_stop()
        except Exception:  # noqa: BLE001 - teardown must never raise
            pass
        self._connected.clear()

    def is_connected(self):
        return self._client.is_connected()

    def is_usable(self):
        """True when this connection may still be used for publishes."""
        return not self._failed and self._client.is_connected()

    def simulate_network_drop(self):
        """Abrupt socket close without MQTT DISCONNECT (test hook for
        reconnect/backoff behavior; devices do the equivalent on power loss)."""
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
        self._connected.clear()

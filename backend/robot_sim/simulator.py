"""The simulator runtime: real paho-mqtt client + background loops that
mirror the firmware's device-side behavior end to end.

Contract parity with the firmware:
- MQTT username = device_id, password = device Bearer JWT (challenge flow)
- LWT: retained QoS1 ``{"online":false}`` on the own status topic
- subscribes to exactly four own topics (command + three notify markers)
- heartbeat: literal ``{}`` at QoS0 on its own schedule
- status: retained QoS1 partial updates (online/state only - no radio or
  battery exists in software, so rssi/battery_percent are never emitted)
- state machine BOOT -> <action> -> IDLE, exactly like actions.c
- command acks: command.received -> (simulated execution) -> command.executed,
  rejects immediately with command.rejected; every payload carries
  ``"simulated": true`` so logs/events cannot be mistaken for hardware
- bounded exponential reconnect; token refresh before expiry followed by
  an MQTT reconnect (username_pw_set + reconnect), mirroring the firmware
"""

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass

import paho.mqtt.client as mqtt

from mqtt_bridge import topics

from . import protocol
from .backend_api import BackendError, token_refresh_due

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 10.0
SUBACK_TIMEOUT_SECONDS = 5.0
MAX_CONNECT_ATTEMPTS = 6
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 30.0
THREAD_JOIN_TIMEOUT_SECONDS = 5.0
COMMAND_QUEUE_MAX = 8


@dataclass(frozen=True)
class SimulatorConfig:
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    heartbeat_seconds: float = 30.0
    status_seconds: float = 60.0
    exec_delay_seconds: float = 0.3
    keepalive_seconds: int = 30


def backoff_delay(attempt):
    """Deterministic bounded exponential backoff (no jitter; no Django
    settings dependency - the CLI runs without them)."""
    return min(
        BACKOFF_CAP_SECONDS,
        BACKOFF_BASE_SECONDS * (2 ** max(0, int(attempt))),
    )


class RobotSimulator:
    """Single-use: call start() once, stop() once (stop() is idempotent)."""

    def __init__(self, identity, api, config=None, *, on_pairing_code=None):
        self._identity = identity
        self._api = api
        self._config = config or SimulatorConfig()
        self._on_pairing_code = on_pairing_code
        self._device_id = identity.device_id
        self._command_topic = topics.command_topic(identity.device_id)

        self._client = None
        self._stop = threading.Event()
        self._connect_done = threading.Event()
        self._subscribed = threading.Event()
        self._connect_error = None
        self._started_once = False
        self._first_connect_done = False

        self._state = protocol.STATE_BOOT
        self._token = None
        self._token_issued = 0.0
        self._token_expires = 0

        self._commands = queue.Queue(maxsize=COMMAND_QUEUE_MAX)
        self._threads = []

        # Assertion/debug surface: which filters this device actually
        # asked for, and what the broker granted (mirrors MqttTestClient).
        self.subscribed_topics = []
        self.subscription_ack_codes = []

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self, timeout=CONNECT_TIMEOUT_SECONDS):
        if self._started_once:
            raise RuntimeError("RobotSimulator instances are single-use")
        self._started_once = True

        pairing_code = self._api.register()
        if pairing_code is not None and self._on_pairing_code is not None:
            self._on_pairing_code(pairing_code)

        self._adopt_token(*self._api.fetch_token())
        self._client = self._build_client()
        self._connect_with_backoff()
        self._client.loop_start()

        if not self._connect_done.wait(timeout):
            self._shutdown_client()
            raise BackendError("timed out waiting for MQTT CONNACK")
        if self._connect_error:
            error = self._connect_error
            self._shutdown_client()
            raise BackendError(error)
        if not self._subscribed.wait(SUBACK_TIMEOUT_SECONDS):
            self._shutdown_client()
            raise BackendError("device subscriptions were not acknowledged")
        expected_codes = len(protocol.own_subscriptions(self._device_id))
        if (
            len(self.subscription_ack_codes) != expected_codes
            or any(code != 1 for code in self.subscription_ack_codes)
        ):
            codes = list(self.subscription_ack_codes)
            self._shutdown_client()
            raise BackendError(f"broker denied device subscriptions: {codes}")

        self._spawn(self._heartbeat_loop)
        self._spawn(self._status_loop)
        self._spawn(self._command_worker)
        logger.info(
            "simulator connected: device_id=%s host=%s port=%s",
            self._device_id,
            self._config.mqtt_host,
            self._config.mqtt_port,
        )

    def stop(self):
        if self._client is None:
            return
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=THREAD_JOIN_TIMEOUT_SECONDS)
        self._threads = []
        # Graceful shutdown publishes the same retained offline marker the
        # LWT would publish, so the dashboard sees online=false immediately.
        self._publish_status(protocol.OFFLINE_STATUS_JSON, wait=True)
        self._shutdown_client()
        logger.info("simulator stopped: device_id=%s", self._device_id)

    @property
    def state(self):
        return self._state

    # ------------------------------------------------------------------
    # paho callbacks (VERSION2 signatures)
    # ------------------------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            if not self._first_connect_done:
                self._connect_error = (
                    f"broker rejected MQTT connection ({reason_code})"
                )
            self._connect_done.set()
            return
        # One SUBSCRIBE packet for all four filters -> one SUBACK carrying
        # every granted code (so a partial denial cannot go unnoticed).
        subscriptions = protocol.own_subscriptions(self._device_id)
        client.subscribe(list(subscriptions))
        if not self.subscribed_topics:
            self.subscribed_topics = [topic for topic, _qos in subscriptions]
        self._publish_status(protocol.status_json(self._state))
        if not self._first_connect_done:
            self._first_connect_done = True
            self._publish_event(
                protocol.lifecycle_event(
                    "simulator.started", {"device_id": self._device_id}
                )
            )
        self._connect_done.set()

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties):
        self.subscription_ack_codes = [
            rc.value if hasattr(rc, "value") else int(rc) for rc in reason_codes
        ]
        self._subscribed.set()

    def _on_disconnect(
        self, client, userdata, disconnect_flags, reason_code, properties
    ):
        if not self._stop.is_set():
            logger.warning("MQTT disconnected (%s); paho will retry", reason_code)

    def _on_message(self, client, userdata, message):
        topic = message.topic
        if topic == self._command_topic:
            try:
                self._commands.put_nowait(bytes(message.payload))
            except queue.Full:
                logger.warning("command queue full; command dropped")
            return
        # Notify markers (config/ota/content): log the topic only.
        logger.debug("notify received: %s", topic)

    # ------------------------------------------------------------------
    # background loops
    # ------------------------------------------------------------------
    def _heartbeat_loop(self):
        while not self._stop.wait(self._config.heartbeat_seconds):
            self._publish(
                topics.heartbeat_topic(self._device_id),
                protocol.HEARTBEAT_PAYLOAD,
                qos=topics.QOS_HEARTBEAT,
                retain=topics.RETAINED_HEARTBEAT,
            )
            self._maybe_refresh_token()

    def _status_loop(self):
        while not self._stop.wait(self._config.status_seconds):
            self._publish_status(protocol.status_json(self._state))

    def _command_worker(self):
        while not self._stop.is_set():
            try:
                raw = self._commands.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._process_command(raw)
            except Exception:  # noqa: BLE001 - worker must never die
                logger.exception("command processing failed")

    def _process_command(self, raw):
        action, reject_event = protocol.evaluate_command(raw)
        if reject_event is not None:
            logger.info(
                "command rejected: reason=%s",
                reject_event["payload"].get("reason"),
            )
            self._publish_event(reject_event)
            return
        logger.info("command received: action=%s (simulated)", action)
        self._publish_event(protocol.command_event("command.received", action=action))
        self._set_state(action)
        if self._stop.wait(self._config.exec_delay_seconds):
            return
        self._publish_event(protocol.command_event("command.executed", action=action))
        self._set_state(protocol.STATE_IDLE)
        logger.info("command executed: action=%s (simulated)", action)

    # ------------------------------------------------------------------
    # token / publishing helpers
    # ------------------------------------------------------------------
    def _adopt_token(self, token, expires_in):
        self._token = token
        self._token_expires = int(expires_in)
        self._token_issued = time.monotonic()

    def _maybe_refresh_token(self):
        if self._client is None:
            return
        if not token_refresh_due(
            issued_monotonic=self._token_issued,
            expires_in=self._token_expires,
            now_monotonic=time.monotonic(),
        ):
            return
        try:
            token, expires_in = self._api.fetch_token()
        except BackendError as exc:
            logger.warning("token refresh failed (%s); will retry", exc)
            return
        self._adopt_token(token, expires_in)
        try:
            self._client.username_pw_set(self._device_id, token)
            self._client.reconnect()
            logger.info("device token refreshed; MQTT reconnected")
        except Exception as exc:  # noqa: BLE001 - retry on next tick
            logger.warning(
                "reconnect after token refresh failed (%s); will retry",
                type(exc).__name__,
            )

    def _set_state(self, state):
        self._state = state
        self._publish_status(protocol.status_json(state))

    def _publish_status(self, payload, wait=False):
        self._publish(
            topics.status_topic(self._device_id),
            payload,
            qos=topics.QOS_STATUS,
            retain=topics.RETAINED_STATUS,
            wait=wait,
        )

    def _publish_event(self, event):
        self._publish(
            topics.event_topic(self._device_id),
            json.dumps(event, separators=(",", ":")),
            qos=topics.QOS_EVENT,
            retain=topics.RETAINED_EVENT,
        )

    def _publish(self, topic, payload, qos, retain=False, wait=False):
        if self._client is None:
            return False
        try:
            info = self._client.publish(topic, payload, qos=qos, retain=retain)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.warning(
                    "publish not accepted (rc=%s) on %s", info.rc, topic
                )
                return False
            if wait:
                info.wait_for_publish(timeout=THREAD_JOIN_TIMEOUT_SECONDS)
            return True
        except Exception:  # noqa: BLE001 - transport down must not crash
            logger.warning("publish failed on %s (transport down)", topic)
            return False

    # ------------------------------------------------------------------
    # connection setup
    # ------------------------------------------------------------------
    def _build_client(self):
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self._device_id,
            clean_session=True,
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set(self._device_id, self._token)
        client.will_set(
            topics.status_topic(self._device_id),
            protocol.OFFLINE_STATUS_JSON,
            qos=topics.QOS_STATUS,
            retain=topics.RETAINED_STATUS,
        )
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        client.on_connect = self._on_connect
        client.on_subscribe = self._on_subscribe
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        return client

    def _connect_with_backoff(self):
        last_error = None
        for attempt in range(MAX_CONNECT_ATTEMPTS):
            try:
                self._client.connect(
                    self._config.mqtt_host,
                    self._config.mqtt_port,
                    keepalive=self._config.keepalive_seconds,
                )
                return
            except OSError as exc:
                last_error = exc
                if attempt + 1 >= MAX_CONNECT_ATTEMPTS:
                    break
                delay = backoff_delay(attempt)
                logger.warning(
                    "MQTT connect attempt %s failed (%s); retrying in %.0fs",
                    attempt + 1,
                    type(exc).__name__,
                    delay,
                )
                self._stop.wait(delay)
        raise BackendError(
            f"MQTT broker unreachable at {self._config.mqtt_host}:"
            f"{self._config.mqtt_port} ({type(last_error).__name__})"
        )

    def _shutdown_client(self):
        # Mark stopping first so on_disconnect never warns during an
        # intentional teardown (start() failure or stop()).
        self._stop.set()
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001 - teardown must not raise
            pass
        try:
            client.loop_stop()
        except Exception:  # noqa: BLE001 - teardown must not raise
            pass

    def _spawn(self, target):
        thread = threading.Thread(
            target=target, name=f"robot-sim-{target.__name__}", daemon=True
        )
        thread.start()
        self._threads.append(thread)

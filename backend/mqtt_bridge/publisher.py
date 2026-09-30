import json
import logging
import os
import threading

from django.conf import settings

from . import topics
from .client import MqttConnection, MqttError
from .payloads import DeviceCommandSerializer, serialize_notify_payload

logger = logging.getLogger(__name__)

_publisher = None
_publisher_lock = threading.Lock()


class MqttPublisher:
    def __init__(self, connection):
        self.connection = connection

    def publish_command(self, robot, payload):
        """QoS 1, not retained (Section 11.2): momentary delivery to exactly
        this robot's command topic. Raises ValueError on a malformed
        envelope and MqttError on transport failure."""
        data = dict(payload)
        DeviceCommandSerializer(data=data).is_valid(raise_exception=True)
        self.connection.publish(
            topics.command_topic(robot.device_id),
            _dump_json(data),
            qos=topics.QOS_COMMAND,
            retain=topics.RETAINED_COMMAND,
        )
        logger.info(
            "mqtt command published",
            extra={"device_id": robot.device_id},
        )

    def publish_notify(self, robot, kind, version):
        """QoS 1, retained version marker (Section 11.2)."""
        if kind not in topics.NOTIFY_KINDS:
            raise ValueError(f"unknown notify kind: {kind!r}")
        self.connection.publish(
            topics.notify_topic(robot.device_id, kind),
            serialize_notify_payload(version),
            qos=topics.QOS_NOTIFY,
            retain=topics.RETAINED_NOTIFY,
        )
        logger.info(
            "mqtt notify published",
            extra={"device_id": robot.device_id, "notify_kind": kind},
        )


def get_publisher():
    """Lazy singleton; one bounded connect attempt per creation. Transport
    failure raises MqttError (views map it to 503)."""
    global _publisher
    with _publisher_lock:
        if _publisher is None:
            connection = MqttConnection(
                client_id=f"django-publisher-{os.getpid()}",
                username=settings.MQTT_BRIDGE_USERNAME,
                password=settings.MQTT_BRIDGE_PASSWORD,
            )
            connection.connect()
            _publisher = MqttPublisher(connection)
    return _publisher


def reset_publisher():
    """Drop the singleton (test teardown / explicit reconnect)."""
    global _publisher
    with _publisher_lock:
        if _publisher is not None:
            try:
                _publisher.connection.disconnect()
            except MqttError:
                pass
            _publisher = None


def publish_command(robot, payload):
    get_publisher().publish_command(robot, payload)


def publish_config_notify(robot, version):
    get_publisher().publish_notify(robot, "config", version)


def publish_ota_notify(robot, version):
    get_publisher().publish_notify(robot, "ota", version)


def publish_content_notify(robot, version):
    get_publisher().publish_notify(robot, "content", version)


def _dump_json(data):
    return json.dumps(data)

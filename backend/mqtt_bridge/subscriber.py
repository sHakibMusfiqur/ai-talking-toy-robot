import json
import logging
import os
import time

from django.conf import settings
from django.db import close_old_connections
from rest_framework import serializers

from devices import services as device_services
from devices.models import Robot

from . import topics
from .client import MqttConnection, MqttError, backoff_delay
from .payloads import (
    EventPayloadSerializer,
    HeartbeatPayloadSerializer,
    StatusPayloadSerializer,
    parse_inbound_payload,
)
from .provisioning import ensure_bridge_client

logger = logging.getLogger(__name__)


class MqttBridgeSubscriber:
    """Runs on the paho network thread: one message → validate → one service
    call. Failures are logged by topic/category only and never propagate
    (a bad message must not take down the connection loop)."""

    def __init__(self, connection=None):
        self.connection = connection

    def handle_message(self, client, userdata, message):
        try:
            close_old_connections()
            self._handle(message.topic, message.payload)
        except Exception:
            logger.exception(
                "mqtt bridge failed to handle message",
                extra={"mqtt_topic": message.topic},
            )
        finally:
            close_old_connections()

    def _handle(self, topic, raw):
        parsed = topics.parse_topic(topic)
        if parsed is None:
            logger.warning(
                "mqtt message on unrecognized topic dropped",
                extra={"mqtt_topic": topic},
            )
            return
        device_id, category, _kind = parsed
        if category not in topics.DEVICE_PUBLISH_CATEGORIES:
            # Devices may only publish status/heartbeat/event (Section 11.2);
            # ACL blocks this, so reaching here is a defense-in-depth drop.
            logger.warning(
                "mqtt message on unexpected category dropped",
                extra={"mqtt_topic": topic},
            )
            return
        robot = Robot.objects.filter(device_id=device_id).first()
        if robot is None:
            logger.warning(
                "mqtt message for unknown device dropped",
                extra={"device_id": device_id, "mqtt_category": category},
            )
            return
        if robot.pairing_status != Robot.PAIRING_PAIRED:
            logger.warning(
                "mqtt message for unpaired device dropped",
                extra={"device_id": device_id, "mqtt_category": category},
            )
            return
        if category == topics.STATUS:
            self._handle_status(robot, raw)
        elif category == topics.HEARTBEAT:
            self._handle_heartbeat(robot, raw)
        else:
            self._handle_event(robot, raw)

    def _handle_status(self, robot, raw):
        data = _decode(raw)
        if data is None:
            return
        serializer = StatusPayloadSerializer(data=data)
        if not serializer.is_valid():
            _reject(robot, topics.STATUS, serializer.errors)
            return
        device_services.update_status(robot=robot, **serializer.validated_data)

    def _handle_heartbeat(self, robot, raw):
        stripped = raw.strip() if isinstance(raw, (bytes, bytearray)) else raw
        data = {} if stripped in (b"", "") else _decode(raw)
        if data is None:
            return
        serializer = HeartbeatPayloadSerializer(data=data)
        if not serializer.is_valid():
            _reject(robot, topics.HEARTBEAT, serializer.errors)
            return
        device_services.record_heartbeat(robot=robot)

    def _handle_event(self, robot, raw):
        data = _decode(raw)
        if data is None:
            return
        serializer = EventPayloadSerializer(data=data)
        if not serializer.is_valid():
            _reject(robot, topics.EVENT, serializer.errors)
            return
        device_services.record_robot_event(
            robot=robot,
            event_type=serializer.validated_data["event_type"],
            payload=serializer.validated_data.get("payload", {}),
        )


def _decode(raw):
    try:
        return parse_inbound_payload(raw)
    except ValueError as exc:
        logger.warning(
            "mqtt payload rejected",
            extra={"reason": str(exc)},
        )
        return None


def _reject(robot, category, errors):
    logger.warning(
        "mqtt payload failed validation",
        extra={
            "device_id": robot.device_id,
            "mqtt_category": category,
            "errors": json.dumps(errors, default=str)[:500],
        },
    )


def create_bridge_connection(subscriber):
    connection = MqttConnection(
        client_id=f"django-bridge-{os.getpid()}",
        username=settings.MQTT_BRIDGE_USERNAME,
        password=settings.MQTT_BRIDGE_PASSWORD,
        on_message=subscriber.handle_message,
    )
    subscriber.connection = connection
    return connection


def start_bridge():
   
    ensure_bridge_client()
    subscriber = MqttBridgeSubscriber()
    connection = create_bridge_connection(subscriber)
    connection.subscribe(topics.BACKEND_SUBSCRIPTIONS)
    attempt = 0
    while True:
        try:
            connection.connect()
            return subscriber
        except MqttRejected as exc:
            connection.disconnect()
            raise MqttError(str(exc)) from exc
        except MqttError as exc:
            logger.error(
                "mqtt bridge connect attempt failed",
                extra={"attempt": attempt, "error": str(exc)},
            )
            connection.disconnect()
            time.sleep(backoff_delay(attempt))
            attempt += 1

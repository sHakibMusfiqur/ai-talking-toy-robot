from bson import ObjectId
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Parent
from core.permissions import AuthRequired, IsAdminRole
from core.semantic_actions import validate_semantic_action
from mqtt_bridge.client import MqttError
from mqtt_bridge.payloads import DeviceCommandSerializer
from mqtt_bridge.publisher import publish_command

from .authentication import DeviceJWTAuthentication
from .exceptions import RobotCommandThrottled, RobotCommandUnavailable
from .models import Robot, RobotState
from .permissions import DeviceAuthenticated
from .serializers import (
    ConfigurationReadSerializer,
    ControllerStateSerializer,
    DeviceChallengeSerializer,
    DevicePairSerializer,
    DeviceRegisterSerializer,
    DeviceTokenSerializer,
    RobotEventReadSerializer,
    RobotSerializer,
    SensorDataReadSerializer,
)
from .services import (
    audit_robot_command,
    create_device_challenge,
    exchange_device_token,
    pair_robot,
    register_device,
)

import itertools
import threading
import time as _time


_command_cooldowns: dict = {}  # (user_pk, robot_pk) -> (monotonic_ts, token)
# Guards _command_cooldowns only, for the microsecond check+reserve and
# release critical sections - never while MQTT publishing is in flight
# (Phase A concurrency fix: non-atomic check-then-act allowed concurrent
# duplicate publishes within the cooldown window).
_command_cooldown_lock = threading.Lock()
_reservation_tokens = itertools.count(1)
# Returned by _reserve_command_slot when suppression is disabled: publish
# proceeds and no reservation is stored (release is a no-op for it).
_COOLDOWN_DISABLED = object()


def _reserve_command_slot(user, robot):
    """Atomically check the cooldown and reserve the next publish slot.

    Returns a unique reservation token when the caller may publish (a fresh
    reservation was taken), None when the (user, robot) pair is still
    inside its window (throttled), or _COOLDOWN_DISABLED when suppression is
    off. Reserving BEFORE publishing makes concurrent requests serialise on
    the reservation instead of both passing a non-atomic check-then-act
    pair; a failed publish must call _release_command_slot with this token
    so retries remain possible WITHOUT deleting a newer reservation that
    replaced an expired one."""
    window = settings.ROBOT_COMMAND_COOLDOWN_SECONDS
    if window <= 0:
        return _COOLDOWN_DISABLED
    key = (user.pk, str(robot.pk))
    now = _time.monotonic()
    with _command_cooldown_lock:
        entry = _command_cooldowns.get(key)
        if entry is not None and (now - entry[0]) < window:
            return None
        token = next(_reservation_tokens)
        _command_cooldowns[key] = (now, token)
        if len(_command_cooldowns) > 1000:
            expired = [
                stale
                for stale, (ts, _tok) in _command_cooldowns.items()
                if now - ts >= window
            ]
            for stale in expired:
                _command_cooldowns.pop(stale, None)
        return token


def _release_command_slot(user, robot, token) -> None:
    """Drop a reservation taken by _reserve_command_slot (publish failed,
    so the user must be able to retry immediately). Only the reservation
    holding this exact token is removed: if the entry expired and a newer
    request reserved the slot in the meantime, the newer reservation is
    left untouched (an unconditional pop would let a slow failing publish
    release someone else's newer reservation)."""
    if token is None or token is _COOLDOWN_DISABLED:
        return
    with _command_cooldown_lock:
        entry = _command_cooldowns.get((user.pk, str(robot.pk)))
        if entry is not None and entry[1] == token:
            _command_cooldowns.pop((user.pk, str(robot.pk)), None)


def _client_ip(request):
    return request.META.get("REMOTE_ADDR") or None


def _get_parent(request):
    parent, _ = Parent.objects.get_or_create(user=request.user)
    return parent


def _get_owned_robot(request, pk):
    """Owner-scoped robot resolution for read APIs and panel pages: malformed,
    unknown and non-owned ids are indistinguishable (404), the same rule as
    the robot detail endpoint — resource existence is never leaked across
    parents. Raises Http404 so one helper serves both plain Django views
    (Django's 404 handler) and DRF views (DRF converts Http404 to the same
    404 `{"detail": "Not found."}` response)."""
    if not isinstance(pk, str) or not ObjectId.is_valid(pk):
        raise Http404()
    parent = _get_parent(request)
    robot = Robot.objects.filter(pk=ObjectId(pk), parent=parent).first()
    if robot is None:
        raise Http404()
    return robot


LIST_DEFAULT_LIMIT = 20
LIST_MAX_LIMIT = 100
SUMMARY_EVENTS_LIMIT = 10


def _bounded_limit(request):
    raw = request.query_params.get("limit")
    if raw is None:
        return LIST_DEFAULT_LIMIT
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        raise ValidationError({"limit": "Must be an integer."})
    if limit < 1 or limit > LIST_MAX_LIMIT:
        raise ValidationError(
            {"limit": f"Must be between 1 and {LIST_MAX_LIMIT}."}
        )
    return limit


def _state_payload(robot):
    state_obj = RobotState.objects.filter(robot=robot).first()
    if state_obj is None:
        raw = {
            "online": None,
            "state": None,
            "battery_percent": None,
            "rssi": None,
            "last_updated": None,
        }
    else:
        raw = {
            "online": state_obj.online,
            "state": state_obj.state,
            "battery_percent": state_obj.battery_percent,
            "rssi": state_obj.rssi,
            "last_updated": state_obj.last_updated,
        }
    raw["last_seen"] = robot.last_seen
    raw["firmware_version"] = robot.firmware_version
    return ControllerStateSerializer(raw).data


# Defense-in-depth: configuration data must never expose credential
# material, whoever writes it (read APIs never expose secrets).
CONFIG_SECRET_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "api_key",
    "private_key",
    "access_key",
    "authorization",
)


def _redact_config_data(value):
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if any(marker in str(key).lower() for marker in CONFIG_SECRET_MARKERS):
                redacted[key] = "[redacted]"
            else:
                redacted[key] = _redact_config_data(item)
        return redacted
    if isinstance(value, list):
        return [_redact_config_data(item) for item in value]
    return value


def _configuration_payload(robot):
    current = (
        robot.configurations.filter(is_current=True).order_by("-version").first()
    )
    if current is None:
        raw = {"version": None, "data": None, "is_current": None}
    else:
        raw = {
            "version": current.version,
            "data": _redact_config_data(current.data),
            "is_current": current.is_current,
        }
    return ConfigurationReadSerializer(raw).data


class DeviceRegisterView(APIView):
    """Phase 6 device self-registration (Flow 4). Unauthenticated by design —
    the device has no credentials yet; Phase 7 adds challenge/token auth on
    top of the stored public key."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = DeviceRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        robot, pairing_code = register_device(
            device_id=serializer.validated_data["device_id"],
            public_key=serializer.validated_data["public_key"],
            firmware_version=serializer.validated_data["firmware_version"],
            ip=_client_ip(request),
        )
        return Response(
            {
                "id": str(robot.pk),
                "device_id": robot.device_id,
                "pairing_status": robot.pairing_status,
                "pairing_code": pairing_code,
            },
            status=status.HTTP_201_CREATED,
        )


class DevicePairView(APIView):
    """Dashboard-side pairing: an authenticated account claims exactly one
    robot by entering that robot's own single-use pairing code."""

    permission_classes = [AuthRequired]

    def post(self, request):
        serializer = DevicePairSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        parent = _get_parent(request)
        nickname = serializer.validated_data.get("nickname") or None
        robot = pair_robot(
            pairing_code=serializer.validated_data["pairing_code"],
            parent=parent,
            nickname=nickname,
            ip=_client_ip(request),
        )
        return Response(
            RobotSerializer(robot).data,
            status=status.HTTP_200_OK,
        )


class DeviceChallengeView(APIView):
    """Phase 7 step 1 (AR-10): issue a short-lived signed-challenge nonce
    to a registered device. Unauthenticated by design (the device has no
    token yet); the raw challenge is returned once and never logged."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = DeviceChallengeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        challenge_id, challenge, expires_in = create_device_challenge(
            device_id=serializer.validated_data["device_id"],
            ip=_client_ip(request),
        )
        return Response(
            {
                "challenge_id": challenge_id,
                "challenge": challenge,
                "expires_in": expires_in,
            },
            status=status.HTTP_200_OK,
        )


class DeviceTokenView(APIView):
    """Phase 7 step 2 (AR-10): exchange a signed challenge for a
    short-lived device Bearer JWT. All credential faults return one
    uniform 401 body; the token itself is the only secret returned."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = DeviceTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token, expires_in = exchange_device_token(
            device_id=serializer.validated_data["device_id"],
            challenge_id=serializer.validated_data["challenge_id"],
            challenge=serializer.validated_data["challenge"],
            signature=serializer.validated_data["signature"],
            ip=_client_ip(request),
        )
        return Response(
            {
                "access_token": token,
                "token_type": "Bearer",
                "expires_in": expires_in,
            },
            status=status.HTTP_200_OK,
        )


class DeviceIdentityView(APIView):
    """Authenticated device reads exactly its own robot identity. The
    principal comes solely from the JWT — client-supplied ids in query,
    body or headers can never switch context."""

    authentication_classes = [DeviceJWTAuthentication]
    permission_classes = [DeviceAuthenticated]
    http_method_names = ["get", "head", "options"]

    def get(self, request):
        robot = request.auth["robot"]
        return Response(RobotSerializer(robot).data)


class RobotDetailView(APIView):
    """Owner-scoped robot detail: non-owned, unknown, and malformed ids are
    indistinguishable (404). Write methods are not part of the Phase 6
    contract (405)."""

    permission_classes = [AuthRequired]
    http_method_names = ["get", "head", "options"]

    def get(self, request, pk):
        robot = _get_owned_robot(request, pk)
        return Response(RobotSerializer(robot).data)


class RobotCommandView(APIView):
    """Phase 8 command origin (Section 26): a parent (own robot only) or
    admin (role-scoped, any robot) publishes a semantic command envelope
    through mqtt_bridge.publish_command — the view never touches the MQTT
    client itself. Envelope shape and semantic membership are both
    validated before publishing (unknown/raw-hardware actions are 400), and
    admin commands are audit-logged. Non-owned/unknown/malformed ids are
    indistinguishable (404). Device Bearer tokens are not accepted here
    (session-only view): commands originate from authenticated,
    ownership-checked humans."""

    permission_classes = [AuthRequired]
    http_method_names = ["post"]

    def post(self, request, pk):
        if not isinstance(pk, str) or not ObjectId.is_valid(pk):
            raise NotFound()
        parent = _get_parent(request)
        filters = {"pk": ObjectId(pk)}
        if request.user.role != "admin":
            filters["parent"] = parent
        robot = Robot.objects.filter(**filters).first()
        if robot is None:
            raise NotFound()

        serializer = DeviceCommandSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validate_semantic_action(serializer.validated_data["action"])

        # Duplicate-command suppression (Phase A): ownership and body are
        # already validated, so this never leaks robot existence. Check +
        # reserve is atomic per (user, robot) so concurrent requests cannot
        # both publish within the window; the lock is released before MQTT
        # I/O. A failed publish releases only its own reservation token (a
        # 503 must not lock the user out of retrying, and a slow failing
        # publish must never delete a newer reservation that replaced an
        # expired entry).
        token = _reserve_command_slot(request.user, robot)
        if token is None:
            raise RobotCommandThrottled()

        try:
            publish_command(robot, serializer.validated_data)
        except MqttError:
            _release_command_slot(request.user, robot, token)
            raise RobotCommandUnavailable()
        if request.user.role == "admin":
            audit_robot_command(
                actor=request.user, robot=robot, ip=_client_ip(request)
            )
        return Response(
            {"detail": "Command published."},
            status=status.HTTP_200_OK,
        )


class AdminRobotListView(APIView):
    """Basic admin-only device list (docs/01 Phase 6 implementation order)."""

    permission_classes = [AuthRequired, IsAdminRole]
    http_method_names = ["get", "head", "options"]

    def get(self, request):
        robots = Robot.objects.order_by("-created_at")
        return Response(RobotSerializer(robots, many=True).data)


class RobotStateView(APIView):
    """Owner-scoped live telemetry for the control panel (Section 23 info
    view): exactly the fields RobotState/Robot store — never invents
    fields. Nulls mean "not reported yet", not a fabricated value."""

    permission_classes = [AuthRequired]
    http_method_names = ["get", "head", "options"]

    def get(self, request, pk):
        robot = _get_owned_robot(request, pk)
        return Response(_state_payload(robot))


class RobotEventsView(APIView):
    """Owner-scoped robot event history, newest first, bounded by
    ?limit= (1..LIST_MAX_LIMIT, default LIST_DEFAULT_LIMIT)."""

    permission_classes = [AuthRequired]
    http_method_names = ["get", "head", "options"]

    def get(self, request, pk):
        robot = _get_owned_robot(request, pk)
        limit = _bounded_limit(request)
        events = robot.events.order_by("-timestamp", "-pk")[:limit]
        return Response(RobotEventReadSerializer(events, many=True).data)


class RobotSensorsView(APIView):
    """Owner-scoped sensor readings, newest first, same bound contract as
    the event history."""

    permission_classes = [AuthRequired]
    http_method_names = ["get", "head", "options"]

    def get(self, request, pk):
        robot = _get_owned_robot(request, pk)
        limit = _bounded_limit(request)
        readings = robot.sensor_data.order_by("-timestamp", "-pk")[:limit]
        return Response(SensorDataReadSerializer(readings, many=True).data)


class RobotConfigurationView(APIView):
    """Owner-scoped read of the robot's current Configuration version —
    read-only: no write path exists on this endpoint. Secret-keyed values
    in `data` are redacted before leaving the backend."""

    permission_classes = [AuthRequired]
    http_method_names = ["get", "head", "options"]

    def get(self, request, pk):
        robot = _get_owned_robot(request, pk)
        return Response(_configuration_payload(robot))


class RobotControllerSummaryView(APIView):
    """One-shot assembly for the control panel's first load: robot
    identity + latest state + recent events + current configuration —
    composed only from the data the individual read endpoints expose (no
    new fields, no new ownership rules)."""

    permission_classes = [AuthRequired]
    http_method_names = ["get", "head", "options"]

    def get(self, request, pk):
        robot = _get_owned_robot(request, pk)
        events = robot.events.order_by("-timestamp", "-pk")[:SUMMARY_EVENTS_LIMIT]
        return Response(
            {
                "robot": RobotSerializer(robot).data,
                "state": _state_payload(robot),
                "events": RobotEventReadSerializer(events, many=True).data,
                "configuration": _configuration_payload(robot),
            }
        )


def _robot_card(robot, state):
    """Dashboard card payload: only persisted Robot/RobotState fields — a
    missing RobotState row means the robot never reported, so `online` is
    False (the model's own default semantics) and telemetry fields show
    N/A, never fabricated values."""
    online = bool(state.online) if state is not None else False
    battery = state.battery_percent if state is not None else None
    rssi = state.rssi if state is not None else None
    return {
        "id": str(robot.pk),
        "display_name": robot.nickname or robot.device_id,
        "device_id": robot.device_id,
        "online": online,
        "last_seen": robot.last_seen,
        "battery_display": f"{battery}%" if battery is not None else "N/A",
        "rssi_display": str(rssi) if rssi is not None else "N/A",
        "last_seen_display": (
            timezone.localtime(robot.last_seen).strftime("%b %d, %Y %H:%M")
            if robot.last_seen
            else "N/A"
        ),
        "firmware_display": robot.firmware_version or "N/A",
    }


@login_required
def dashboard(request):
    """Main Web Control Panel — overview page (server-rendered initial
    robot list; live data is fetched from the existing REST APIs).

    Ownership: parent-scoped for every role. Non-admin callers see exactly
    the robots linked to their own Parent row (same rule as
    RobotDetailView). Admins are deliberately NOT elevated here — the
    dashboard never widens a parent-scoped queryset, so an admin sees only
    robots they personally own; fleet visibility remains the admin-only
    `GET /api/v1/devices/` API. Online counts derive from RobotState.online
    (device status/LWT semantics); robots with no RobotState row have never
    reported and count as offline."""
    parent = _get_parent(request)
    robots = list(Robot.objects.filter(parent=parent).order_by("-created_at"))
    states = {
        state.robot_id: state
        for state in RobotState.objects.filter(
            robot__in=[robot.pk for robot in robots]
        )
    }
    cards = [_robot_card(robot, states.get(robot.pk)) for robot in robots]
    online_count = sum(1 for card in cards if card["online"])
    with_activity = [card for card in cards if card["last_seen"] is not None]
    recently_active = max(with_activity, key=lambda c: c["last_seen"], default=None)
    return render(
        request,
        "dashboard/index.html",
        {
            "user_email": request.user.email,
            "robot_cards": cards,
            "total_count": len(cards),
            "online_count": online_count,
            "offline_count": len(cards) - online_count,
            "recently_active": recently_active,
        },
    )


@login_required
def robot_controller(request, pk):
    """Controller page for one robot — ownership is resolved server-side
    with the same helper as the read APIs (404 for non-owned, unknown, or
    malformed ids). The rendered id is only a convenience for JavaScript
    requests; backend authorization stays authoritative on every API call."""
    robot = _get_owned_robot(request, pk)
    return render(
        request,
        "dashboard/controller.html",
        {
            "user_email": request.user.email,
            "robot_id": str(robot.pk),
            "display_name": robot.nickname or robot.device_id,
            "device_id": robot.device_id,
            "firmware_display": robot.firmware_version or "N/A",
            "last_seen_display": (
                timezone.localtime(robot.last_seen).strftime("%b %d, %Y %H:%M")
                if robot.last_seen
                else "N/A"
            ),
        },
    )

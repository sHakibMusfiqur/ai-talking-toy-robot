from bson import ObjectId
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Parent
from core.permissions import AuthRequired, IsAdminRole

from .authentication import DeviceJWTAuthentication
from .models import Robot
from .permissions import DeviceAuthenticated
from .serializers import (
    DeviceChallengeSerializer,
    DevicePairSerializer,
    DeviceRegisterSerializer,
    DeviceTokenSerializer,
    RobotSerializer,
)
from .services import (
    create_device_challenge,
    exchange_device_token,
    pair_robot,
    register_device,
)


def _client_ip(request):
    return request.META.get("REMOTE_ADDR") or None


def _get_parent(request):
    parent, _ = Parent.objects.get_or_create(user=request.user)
    return parent


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
        parent = _get_parent(request)
        if not isinstance(pk, str) or not ObjectId.is_valid(pk):
            raise NotFound()
        robot = Robot.objects.filter(pk=ObjectId(pk), parent=parent).first()
        if robot is None:
            raise NotFound()
        return Response(RobotSerializer(robot).data)


class AdminRobotListView(APIView):
    """Basic admin-only device list (docs/01 Phase 6 implementation order)."""

    permission_classes = [AuthRequired, IsAdminRole]
    http_method_names = ["get", "head", "options"]

    def get(self, request):
        robots = Robot.objects.order_by("-created_at")
        return Response(RobotSerializer(robots, many=True).data)

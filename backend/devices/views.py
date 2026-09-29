from bson import ObjectId
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Parent
from core.permissions import AuthRequired, IsAdminRole

from .models import Robot
from .serializers import DevicePairSerializer, DeviceRegisterSerializer, RobotSerializer
from .services import pair_robot, register_device


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

import base64
import json

from bson import ObjectId
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import ChildProfile, Parent
from core.audio_upload import BoundedMemoryAudioUploadHandler
from devices.authentication import DeviceJWTAuthentication
from devices.models import Robot

from .models import Conversation
from .permissions import ConversationTurnPermission, DeviceConversationPermission
from .serializers import (
    ConversationAudioUploadSerializer,
    ConversationSidecarSerializer,
    ConversationTurnSerializer,
)
from .services import run_conversation_turn
from .throttles import ConversationTurnThrottle


def _parse_id(request_data, field, *, required=True):
    if not isinstance(request_data, dict):
        raise ValidationError(
            {"non_field_errors": ["Invalid data. Expected a dictionary."]}
        )
    raw = request_data.get(field)
    if raw is None:
        if required:
            raise ValidationError({field: ["This field is required."]})
        return None
    if not isinstance(raw, str) or not ObjectId.is_valid(raw):
        raise ValidationError({field: ["Not a valid id."]})
    return ObjectId(raw)


def _resolve_context(request, *, robot_id, child_profile_id, conversation_id):
    
    auth = request.auth
    if isinstance(auth, dict) and "robot" in auth:
        robot = auth["robot"]
        if robot.pk != robot_id:
            raise NotFound()
    else:
        parent, _ = Parent.objects.get_or_create(user=request.user)
        robot = Robot.objects.filter(pk=robot_id, parent=parent).first()
        if robot is None:
            raise NotFound()

    parent = robot.parent
    if parent is None:
        # Unpaired robot: no owner, therefore no child profile and no turn.
        raise NotFound()
    child_profile = (
        ChildProfile.objects.filter(pk=child_profile_id, parent=parent).first()
    )
    if child_profile is None:
        raise NotFound()

    conversation = None
    if conversation_id is not None:
        conversation = (
            Conversation.objects.filter(pk=conversation_id, robot=robot).first()
        )
        if conversation is None:
            raise NotFound()
        if (
            conversation.child_profile_id is not None
            and str(conversation.child_profile_id) != str(child_profile.pk)
        ):
            raise NotFound()
    return robot, child_profile, conversation


class ConversationTurnView(APIView):
    """`POST /api/v1/conversations/turn/` - the single AI-calling boundary.

    Accepts a session caller (owner-scoped; admins are not elevated) or a
    device Bearer JWT scoped to its own robot. Validation order: 401 ->
    id syntax 400 -> ownership 404 -> remaining envelope 400. A failed
    AI-service call still answers 200 with the local Section 15.3
    fallback; the response is always the frozen Output Contract.
    """

    authentication_classes = [SessionAuthentication, DeviceJWTAuthentication]
    permission_classes = [ConversationTurnPermission]
    throttle_classes = [ConversationTurnThrottle]
    http_method_names = ["post"]

    def post(self, request):
        robot_id = _parse_id(request.data, "robot_id")
        child_profile_id = _parse_id(request.data, "child_profile_id")
        conversation_id = _parse_id(
            request.data, "conversation_id", required=False
        )
        robot, child_profile, conversation = _resolve_context(
            request,
            robot_id=robot_id,
            child_profile_id=child_profile_id,
            conversation_id=conversation_id,
        )

        serializer = ConversationTurnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data

        result = run_conversation_turn(
            robot=robot,
            child_profile=child_profile,
            conversation=conversation,
            text=validated.get("text"),
            audio=validated.get("audio"),
            language_hint=validated.get("language_hint"),
            mode=validated["mode"],
        )
        return Response(result, status=status.HTTP_200_OK)


def _parse_sidecar(request):
    """Validate the `Conversation-Sidecar` header (Phase 12, Section 10).

    Header-only: no body is read here. Order matches the documented
    flow - sidecar/file validation (400) happens before ownership (404).
    """
    raw = request.headers.get("Conversation-Sidecar")
    if not raw:
        raise ValidationError({"Conversation-Sidecar": ["This header is required."]})
    try:
        payload = json.loads(raw)
    except ValueError:
        raise ValidationError({"Conversation-Sidecar": ["Invalid JSON."]})
    if not isinstance(payload, dict):
        raise ValidationError(
            {"Conversation-Sidecar": ["Invalid data. Expected a dictionary."]}
        )
    serializer = ConversationSidecarSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


class ConversationAudioTurnView(APIView):
    """`POST /api/v1/conversations/turn/audio[/]` - device-only audio.

    Device Bearer JWT only (sessions keep using the JSON turn endpoint);
    multipart with exactly one `audio` file plus the `Conversation-Sidecar`
    header. Validation order: pre-parse Content-Length gate (400, may run
    before authentication - see core.audio_upload) -> 401 -> 429 -> sidecar/
    file validation 400 -> ownership 404 -> processing. The bounded
    in-memory upload handler is installed before the body is read, so
    application-level handling never writes raw audio to disk; the raw
    bytes are forwarded to the AI service once and never persisted
    (`Message.audio_reference` stays empty).
    """

    authentication_classes = [DeviceJWTAuthentication]
    permission_classes = [DeviceConversationPermission]
    throttle_classes = [ConversationTurnThrottle]
    http_method_names = ["post"]

    def post(self, request):
        request._request.upload_handlers = [BoundedMemoryAudioUploadHandler()]

        sidecar = _parse_sidecar(request)
        serializer = ConversationAudioUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        audio_file = serializer.validated_data["audio"]
        audio_file.seek(0)
        audio_b64 = base64.b64encode(audio_file.read()).decode("ascii")

        token_robot = request.auth["robot"]
        robot, child_profile, conversation = _resolve_context(
            request,
            robot_id=token_robot.pk,
            child_profile_id=sidecar["child_profile_id"],
            conversation_id=sidecar.get("conversation_id"),
        )

        result = run_conversation_turn(
            robot=robot,
            child_profile=child_profile,
            conversation=conversation,
            text=None,
            audio=audio_b64,
            language_hint=sidecar.get("language_hint"),
            mode="conversation",
        )
        return Response(result, status=status.HTTP_200_OK)

from bson import ObjectId
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import ChildProfile, Parent
from devices.authentication import DeviceJWTAuthentication
from devices.models import Robot

from .models import Conversation
from .permissions import ConversationTurnPermission
from .serializers import ConversationTurnSerializer
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
    """Owner-scoped resolution of everything the turn touches (404 on any
    miss). A device token never needs to - and never gets to - name a
    robot other than its own."""
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

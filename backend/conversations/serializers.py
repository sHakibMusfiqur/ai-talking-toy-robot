import base64
import binascii

from bson import ObjectId
from django.conf import settings
from rest_framework import serializers

from .ai_contract import LANGUAGE_HINTS, MODES

TURN_FIELDS = frozenset(
    {
        "robot_id",
        "child_profile_id",
        "conversation_id",
        "text",
        "audio",
        "language_hint",
        "mode",
    }
)


class ObjectIdField(serializers.Field):
    default_error_messages = {"invalid": "Not a valid id."}

    def to_representation(self, value):
        return str(value)

    def to_internal_value(self, data):
        if not isinstance(data, str) or not ObjectId.is_valid(data):
            self.fail("invalid")
        return ObjectId(data)


class ConversationTurnSerializer(serializers.Serializer):
    """The public side of a conversation turn (Section 15.2 subset).

    Deliberately absent: `context` - it is assembled server-side (AR-5b),
    so a client can never inject memories or history - and any raw
    hardware vocabulary (AR-17). Unknown fields are rejected, mirroring
    the strict AI-service contract instead of silently ignoring input.
    """

    robot_id = ObjectIdField()
    child_profile_id = ObjectIdField()
    conversation_id = ObjectIdField(required=False)
    text = serializers.CharField(
        required=False,
        allow_blank=False,
        max_length=settings.CONVERSATION_MAX_TEXT_LENGTH,
    )
    audio = serializers.CharField(
        required=False,
        allow_blank=False,
        trim_whitespace=False,
        max_length=settings.CONVERSATION_MAX_AUDIO_BASE64_LENGTH,
    )
    language_hint = serializers.ChoiceField(
        choices=sorted(LANGUAGE_HINTS), required=False, allow_null=True
    )
    mode = serializers.ChoiceField(
        choices=sorted(MODES), required=False, default="conversation"
    )

    def validate_audio(self, value):
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            raise serializers.ValidationError("audio must be valid base64.")
        if not decoded:
            raise serializers.ValidationError(
                "audio must decode to non-empty bytes."
            )
        return value

    def validate(self, attrs):
        if not isinstance(self.initial_data, dict):
            raise serializers.ValidationError(
                {"non_field_errors": ["Invalid data. Expected a dictionary."]}
            )
        unknown = sorted(set(self.initial_data) - TURN_FIELDS)
        if unknown:
            raise serializers.ValidationError(
                {field: ["Unexpected field."] for field in unknown}
            )
        has_text = attrs.get("text") is not None
        has_audio = attrs.get("audio") is not None
        if has_text == has_audio:
            raise serializers.ValidationError(
                {
                    "non_field_errors": [
                        "Exactly one of 'text' or 'audio' must be provided."
                    ]
                }
            )
        return attrs


SIDECAR_FIELDS = frozenset({"conversation_id", "language_hint", "child_profile_id"})


class ConversationSidecarSerializer(serializers.Serializer):
    

    child_profile_id = ObjectIdField()
    conversation_id = ObjectIdField(required=False)
    language_hint = serializers.ChoiceField(
        choices=sorted(LANGUAGE_HINTS), required=False, allow_null=True
    )

    def validate(self, attrs):
        if not isinstance(self.initial_data, dict):
            raise serializers.ValidationError(
                {"non_field_errors": ["Invalid data. Expected a dictionary."]}
            )
        unknown = sorted(set(self.initial_data) - SIDECAR_FIELDS)
        if unknown:
            raise serializers.ValidationError(
                {field: ["Unexpected field."] for field in unknown}
            )
        return attrs


class ConversationAudioUploadSerializer(serializers.Serializer):
    

    audio = serializers.FileField(allow_empty_file=False)

    def validate_audio(self, value):
        if value.size is not None and value.size > settings.AUDIO_UPLOAD_MAX_FILE_BYTES:
            raise serializers.ValidationError(
                "Audio file exceeds the maximum allowed size."
            )
        return value

    def validate(self, attrs):
        unknown = sorted(set(self.initial_data) - {"audio"})
        if unknown:
            raise serializers.ValidationError(
                {field: ["Unexpected field."] for field in unknown}
            )
        return attrs

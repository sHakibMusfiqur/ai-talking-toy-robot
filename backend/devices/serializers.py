from bson import ObjectId
from rest_framework import serializers

from .exceptions import InvalidDeviceId, InvalidPublicKey
from .models import Robot
from .services import _normalize_device_id, _validate_public_key


class ObjectIdField(serializers.Field):
    default_error_messages = {"invalid": "Not a valid id."}

    def to_representation(self, value):
        return str(value)

    def to_internal_value(self, data):
        if not isinstance(data, str) or not ObjectId.is_valid(data):
            self.fail("invalid")
        return ObjectId(data)


class DeviceRegisterSerializer(serializers.Serializer):
    device_id = serializers.CharField()
    public_key = serializers.CharField(
        trim_whitespace=False, style={"input_type": "password"}
    )
    firmware_version = serializers.CharField(
        required=False, allow_blank=True, default=""
    )

    def validate_device_id(self, value):
        try:
            return _normalize_device_id(value)
        except InvalidDeviceId:
            raise serializers.ValidationError("Enter a valid UUID.")

    def validate_public_key(self, value):
        try:
            return _validate_public_key(value)
        except InvalidPublicKey:
            raise serializers.ValidationError(
                "Enter a PEM-encoded public key."
            )


class DevicePairSerializer(serializers.Serializer):
    pairing_code = serializers.CharField()
    nickname = serializers.CharField(
        required=False, allow_blank=True, max_length=100, default=""
    )

    def validate_pairing_code(self, value):
        if not value.strip():
            raise serializers.ValidationError("This field may not be blank.")
        return value.strip()

    def validate_nickname(self, value):
        return value.strip()


class DeviceChallengeSerializer(serializers.Serializer):
    device_id = serializers.CharField()

    def validate_device_id(self, value):
        try:
            return _normalize_device_id(value)
        except InvalidDeviceId:
            raise serializers.ValidationError("Enter a valid UUID.")


class DeviceTokenSerializer(serializers.Serializer):
    device_id = serializers.CharField()
    challenge_id = serializers.CharField()
    challenge = serializers.CharField(
        trim_whitespace=False, style={"input_type": "password"}
    )
    signature = serializers.CharField(
        trim_whitespace=False, style={"input_type": "password"}
    )

    def validate_device_id(self, value):
        try:
            return _normalize_device_id(value)
        except InvalidDeviceId:
            raise serializers.ValidationError("Enter a valid UUID.")


class RobotSerializer(serializers.ModelSerializer):
    id = ObjectIdField(read_only=True)

    class Meta:
        model = Robot
        fields = [
            "id",
            "device_id",
            "nickname",
            "pairing_status",
            "firmware_version",
            "last_seen",
            "paired_at",
        ]

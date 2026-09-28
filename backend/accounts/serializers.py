from bson import ObjectId
from django.contrib.auth import password_validation
from rest_framework import serializers

from .models import ChildProfile, User


class ObjectIdField(serializers.Field):
    default_error_messages = {"invalid": "Not a valid id."}

    def to_representation(self, value):
        return str(value)

    def to_internal_value(self, data):
        if not isinstance(data, str) or not ObjectId.is_valid(data):
            self.fail("invalid")
        return ObjectId(data)


class SignupSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        style={"input_type": "password"},
    )

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def validate_password(self, value):
        password_validation.validate_password(value)
        return value


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        style={"input_type": "password"},
    )

    def validate_email(self, value):
        return value.strip().lower()


class ChildProfileSerializer(serializers.ModelSerializer):
    id = ObjectIdField(read_only=True)

    class Meta:
        model = ChildProfile
        fields = ["id", "first_name", "age_band", "language"]

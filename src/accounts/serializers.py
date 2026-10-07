from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers

from accounts.legal import TERMS_REQUIRED

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "email_verified",
            "avatar_url",
            "bio",
            "terms_accepted_at",
            "privacy_accepted_at",
            "seller_terms_accepted_at",
            "created_at",
        )
        read_only_fields = (
            "id",
            "username",
            "email",
            "email_verified",
            "terms_accepted_at",
            "privacy_accepted_at",
            "seller_terms_accepted_at",
            "created_at",
        )


class PublicProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "avatar_url", "bio", "created_at")
        read_only_fields = fields


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    accept_terms = serializers.BooleanField(write_only=True)

    class Meta:
        model = User
        fields = ("username", "email", "password", "bio", "avatar_url", "accept_terms")

    def validate_accept_terms(self, value):
        if value is not True:
            raise serializers.ValidationError(TERMS_REQUIRED)
        return value

    def validate(self, attrs):
        user = User(username=attrs.get("username", ""), email=attrs.get("email", ""))
        try:
            validate_password(attrs["password"], user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        return attrs

    def create(self, validated_data):
        validated_data.pop("accept_terms")
        password = validated_data.pop("password")
        now = timezone.now()
        user = User(**validated_data)
        user.email_verified = False
        user.terms_accepted_at = now
        user.privacy_accepted_at = now
        user.set_password(password)
        user.save()
        return user


class EmailSerializer(serializers.Serializer):
    email = serializers.EmailField()


class EmailChangeSerializer(serializers.Serializer):
    new_email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class EmailChangeConfirmSerializer(serializers.Serializer):
    token = serializers.CharField()


class UidTokenSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    password = serializers.CharField(write_only=True)

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers

from accounts.legal import TERMS_REQUIRED
from accounts.models import UserSettings

User = get_user_model()

SUPPORTED_LOCALES = ("en", "pl")


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "email_verified",
            "display_name",
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


    def validate_display_name(self, value):
        return " ".join(value.split())

    def validate_avatar_url(self, value):
        # Avatars are set through the upload endpoint; only clearing is allowed here.
        if value:
            raise serializers.ValidationError("Upload an image to set an avatar.")
        return value


class PublicProfileSerializer(serializers.ModelSerializer):
    """Respects the owner's privacy switches. The owner's own view is `UserSerializer`."""

    class Meta:
        model = User
        fields = ("id", "username", "display_name", "avatar_url", "bio", "created_at")
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        prefs = UserSettings.objects.filter(user=instance).first()
        if prefs is not None:
            if not prefs.show_bio:
                data["bio"] = ""
            if not prefs.show_joined:
                data["created_at"] = None
        data["show_games"] = True if prefs is None else prefs.show_games
        return data


class UserSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserSettings
        fields = (
            "locale",
            "theme",
            "newsletter_opt_in",
            "show_bio",
            "show_joined",
            "show_games",
        )

    def validate_locale(self, value):
        if value not in SUPPORTED_LOCALES:
            raise serializers.ValidationError("Unsupported language.")
        return value

    def update(self, instance, validated_data):
        if (
            "newsletter_opt_in" in validated_data
            and validated_data["newsletter_opt_in"] != instance.newsletter_opt_in
        ):
            instance.newsletter_updated_at = timezone.now()
            if not validated_data["newsletter_opt_in"]:
                # Opting out also stops mail from an earlier public-form subscription.
                from newsletter.services import unsubscribe

                unsubscribe(instance.user.email)
        return super().update(instance, validated_data)


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_current_password(self, value):
        if not self.context["user"].check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate(self, attrs):
        user = self.context["user"]
        try:
            validate_password(attrs["new_password"], user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": list(exc.messages)}) from exc
        return attrs


class AvatarUploadSerializer(serializers.Serializer):
    image = serializers.CharField(write_only=True)


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

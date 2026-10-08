from __future__ import annotations

from rest_framework import serializers

from newsletter.models import Issue


class SubscribeSerializer(serializers.Serializer):
    email = serializers.EmailField()


class TokenSerializer(serializers.Serializer):
    token = serializers.CharField()


class IssueSerializer(serializers.ModelSerializer):
    sent_count = serializers.SerializerMethodField()
    failed_count = serializers.SerializerMethodField()

    class Meta:
        model = Issue
        fields = (
            "id",
            "subject",
            "body",
            "created_at",
            "updated_at",
            "sending_started_at",
            "sent_at",
            "sent_count",
            "failed_count",
        )
        read_only_fields = ("created_at", "updated_at", "sending_started_at", "sent_at")

    def get_sent_count(self, obj) -> int:
        return obj.deliveries.filter(sent_at__isnull=False).count()

    def get_failed_count(self, obj) -> int:
        return obj.deliveries.filter(sent_at__isnull=True).exclude(error="").count()

    def validate(self, attrs):
        if self.instance is not None and self.instance.sending_started_at is not None:
            raise serializers.ValidationError("An issue cannot be edited after sending starts.")
        return attrs

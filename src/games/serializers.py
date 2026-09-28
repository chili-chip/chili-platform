from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers

from games.covers import (
    MAX_GAME_DATA_BYTES,
    absolute_media_url,
    assign_cover,
    clear_cover,
    decode_png_data_url,
)
from games.models import Game

_MISSING = object()
_LIBRARY_STATUSES = ("paid", "refunded", "disputed")


def _viewer(serializer):
    request = serializer.context.get("request")
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None


def _can_read_bitsy(serializer, game: Game) -> bool:
    user = _viewer(serializer)
    if user is None:
        return False
    if game.owner_id == user.id:
        return True
    if not serializer.context.get("detail"):
        return False
    from marketplace.models import Purchase

    return Purchase.objects.filter(
        buyer=user,
        game_id=game.pk,
        status__in=_LIBRARY_STATUSES,
    ).exists()


def _listing_slug(serializer, game: Game) -> str:
    try:
        listing = game.listing
    except ObjectDoesNotExist:
        return ""
    if listing.published:
        return listing.slug
    user = _viewer(serializer)
    if user is not None and (game.owner_id == user.id or user.is_staff):
        return listing.slug
    return ""


class GameSerializer(serializers.ModelSerializer):
    owner = serializers.CharField(source="owner.username", read_only=True)
    cover = serializers.CharField(required=False, allow_blank=True)
    listing_slug = serializers.SerializerMethodField()
    in_library = serializers.SerializerMethodField()

    class Meta:
        model = Game
        fields = (
            "id",
            "title",
            "slug",
            "owner",
            "cover",
            "data",
            "released",
            "listing_slug",
            "in_library",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "slug",
            "owner",
            "released",
            "listing_slug",
            "in_library",
            "created_at",
            "updated_at",
        )

    def get_listing_slug(self, game: Game) -> str:
        return _listing_slug(self, game)

    def get_in_library(self, game: Game) -> bool:
        return game.pk in self.context.get("library_ids", ())

    def to_representation(self, instance):
        data = super().to_representation(instance)
        cover = instance.cover
        if cover:
            data["cover"] = absolute_media_url(cover.url, self.context.get("request"))
        else:
            data["cover"] = ""
        if not _can_read_bitsy(self, instance):
            data["data"] = ""
        return data

    def validate_title(self, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("This field may not be blank.")
        return cleaned

    def validate_data(self, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_GAME_DATA_BYTES:
            raise serializers.ValidationError("Game data must be 1.5 MB or smaller.")
        return value

    def validate_cover(self, value: str):
        cleaned = value.strip()
        if not cleaned:
            return None
        return decode_png_data_url(cleaned)

    def create(self, validated_data):
        cover = validated_data.pop("cover", _MISSING)
        game = Game(**validated_data)
        game.save()
        if isinstance(cover, (bytes, bytearray)):
            assign_cover(game, bytes(cover))
            game.save()
        return game

    def update(self, instance, validated_data):
        cover = validated_data.pop("cover", _MISSING)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if cover is not _MISSING:
            if cover is None:
                clear_cover(instance)
            else:
                assign_cover(instance, cover)
        instance.save()
        return instance

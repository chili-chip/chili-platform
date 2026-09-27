from __future__ import annotations

from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils.text import slugify

from games.covers import MAX_COVER_BYTES

SLUG_LENGTH = 180


def game_cover_upload_to(instance, filename: str) -> str:
    game_id = instance.pk or "new"
    return f"games/covers/{game_id}/{uuid4().hex}.png"


def validate_game_cover(value) -> None:
    name = getattr(value, "name", "") or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext and ext != "png":
        raise ValidationError("Cover must be a PNG.")
    size = getattr(value, "size", 0) or 0
    if size > MAX_COVER_BYTES:
        raise ValidationError("Cover must be 8 MB or smaller.")


def build_unique_slug(game: Game) -> str:
    base = (slugify(game.title) or "game")[:160]
    candidate = base
    index = 2
    taken = Game.objects.exclude(pk=game.pk)
    while taken.filter(slug=candidate).exists():
        suffix = f"-{index}"
        candidate = f"{base[: SLUG_LENGTH - len(suffix)]}{suffix}"
        index += 1
    return candidate


class Game(models.Model):
    title = models.CharField(max_length=160)
    slug = models.SlugField(max_length=SLUG_LENGTH, unique=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="games",
    )
    cover = models.FileField(
        upload_to=game_cover_upload_to,
        blank=True,
        max_length=255,
        validators=[validate_game_cover],
        help_text="PNG cover, 8 MB max. Stored in R2; the API returns a media URL.",
    )
    data = models.TextField(help_text="Bitsy game source.")
    released = models.BooleanField(
        default=False,
        help_text="A project is unreleased and cannot be sold. Release keeps this Bitsy data and does not list the game.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]

    def save(self, *args, **kwargs):
        title_changed = False
        if self.pk:
            previous = Game.objects.filter(pk=self.pk).values_list("title", flat=True).first()
            title_changed = previous != self.title
        if not self.slug or title_changed:
            self.slug = build_unique_slug(self)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.title


@receiver(post_delete, sender=Game)
def delete_game_cover(sender, instance: Game, **kwargs) -> None:
    if instance.cover:
        instance.cover.delete(save=False)

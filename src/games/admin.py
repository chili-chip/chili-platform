from django.contrib import admin
from django.utils.html import format_html

from games.models import Game


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "slug", "released", "preview", "updated_at")
    search_fields = ("title", "slug", "owner__username")
    raw_id_fields = ("owner",)
    readonly_fields = ("slug", "created_at", "updated_at")

    @admin.display(description="Cover")
    def preview(self, obj: Game) -> str:
        if not obj.cover:
            return "—"
        return format_html(
            '<img src="{}" alt="" style="height:48px;width:48px;object-fit:cover;background:#111" />',
            obj.cover.url,
        )

from django.contrib import admin

from app.admin_markdown import MarkdownAdminMixin
from community.models import ForumCategory, ForumComment, ForumPost


@admin.register(ForumCategory)
class ForumCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(ForumPost)
class ForumPostAdmin(MarkdownAdminMixin, admin.ModelAdmin):
    markdown_fields = ("content",)
    list_display = ("title", "category", "author", "created_at")
    search_fields = ("title", "content")
    list_filter = ("category",)


@admin.register(ForumComment)
class ForumCommentAdmin(MarkdownAdminMixin, admin.ModelAdmin):
    markdown_fields = ("content",)
    list_display = ("post", "author", "created_at")

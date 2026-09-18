from django.contrib import admin

from community.models import ForumCategory, ForumComment, ForumPost


@admin.register(ForumCategory)
class ForumCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(ForumPost)
class ForumPostAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "author", "created_at")
    search_fields = ("title", "content")
    list_filter = ("category",)


@admin.register(ForumComment)
class ForumCommentAdmin(admin.ModelAdmin):
    list_display = ("post", "author", "created_at")

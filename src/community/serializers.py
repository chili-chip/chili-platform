from __future__ import annotations

from rest_framework import serializers

from community.models import ForumCategory, ForumComment, ForumPost


class ForumCategorySerializer(serializers.ModelSerializer):
    post_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ForumCategory
        fields = ("id", "name", "slug", "description", "post_count")
        read_only_fields = ("id", "slug", "post_count")

    def create(self, validated_data):
        return ForumCategory.objects.create(**validated_data)


class AuthorSnippetSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    avatar_url = serializers.CharField(allow_blank=True)


class ForumPostSerializer(serializers.ModelSerializer):
    author = AuthorSnippetSerializer(read_only=True)
    category_detail = ForumCategorySerializer(source="category", read_only=True)
    comment_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ForumPost
        fields = (
            "id",
            "title",
            "slug",
            "content",
            "author",
            "category",
            "category_detail",
            "comment_count",
            "created_at",
        )
        read_only_fields = ("id", "slug", "author", "category_detail", "comment_count", "created_at")


class ForumCommentSerializer(serializers.ModelSerializer):
    author = AuthorSnippetSerializer(read_only=True)

    class Meta:
        model = ForumComment
        fields = ("id", "post", "author", "content", "created_at")
        read_only_fields = ("id", "author", "created_at")

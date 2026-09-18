from __future__ import annotations

from django.db.models import Count
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from community.models import ForumCategory, ForumComment, ForumPost
from community.serializers import (
    ForumCategorySerializer,
    ForumCommentSerializer,
    ForumPostSerializer,
)


class IsAdminOrReadOnly(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_staff)


class IsAuthorOrReadOnly(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return obj.author_id == request.user.id or request.user.is_staff


class ForumCategoryViewSet(viewsets.ModelViewSet):
    serializer_class = ForumCategorySerializer
    permission_classes = [IsAdminOrReadOnly]
    lookup_field = "slug"
    queryset = ForumCategory.objects.annotate(post_count=Count("posts"))


class ForumPostViewSet(viewsets.ModelViewSet):
    serializer_class = ForumPostSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsAuthorOrReadOnly]
    queryset = ForumPost.objects.select_related("author", "category").annotate(
        comment_count=Count("comments")
    )

    def perform_create(self, serializer):
        serializer.save(author=self.request.user)

    def get_queryset(self):
        queryset = super().get_queryset()
        category = self.request.query_params.get("category")
        if category:
            queryset = queryset.filter(category__slug=category)
        return queryset

    @action(detail=True, methods=["get", "post"], url_path="comments")
    def comments(self, request, pk=None):
        post = self.get_object()
        if request.method == "GET":
            comments = post.comments.select_related("author")
            return Response(ForumCommentSerializer(comments, many=True).data)

        if not request.user.is_authenticated:
            return Response({"detail": "Authentication required."}, status=401)

        payload = {**request.data, "post": post.id}
        serializer = ForumCommentSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        serializer.save(author=request.user, post=post)
        return Response(serializer.data, status=201)


class ForumCommentViewSet(viewsets.ModelViewSet):
    serializer_class = ForumCommentSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsAuthorOrReadOnly]
    queryset = ForumComment.objects.select_related("author", "post")

    def perform_create(self, serializer):
        serializer.save(author=self.request.user)

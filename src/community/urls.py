from django.urls import include, path
from rest_framework.routers import DefaultRouter

from community.views import ForumCategoryViewSet, ForumCommentViewSet, ForumPostViewSet

router = DefaultRouter()
router.register("categories", ForumCategoryViewSet, basename="forum-category")
router.register("posts", ForumPostViewSet, basename="forum-post")
router.register("comments", ForumCommentViewSet, basename="forum-comment")

urlpatterns = [
    path("", include(router.urls)),
]

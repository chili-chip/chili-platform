from django.urls import path
from rest_framework.routers import DefaultRouter

from newsletter.views import ConfirmView, IssueViewSet, SubscribeView, UnsubscribeView

router = DefaultRouter()
router.register("issues", IssueViewSet, basename="newsletter-issue")

urlpatterns = [
    path("subscribe/", SubscribeView.as_view(), name="newsletter-subscribe"),
    path("confirm/", ConfirmView.as_view(), name="newsletter-confirm"),
    path("unsubscribe/", UnsubscribeView.as_view(), name="newsletter-unsubscribe"),
    *router.urls,
]

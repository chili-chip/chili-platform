from django.urls import include, path
from rest_framework.routers import SimpleRouter

from marketplace.views import (
    AccountSessionView,
    CategoryListView,
    CheckoutConfirmView,
    CheckoutView,
    ConfigView,
    ConnectedAccountView,
    LibraryView,
    ListingViewSet,
    PayoutView,
    RefundView,
    SalesView,
    TagListView,
)

router = SimpleRouter()
router.register("listings", ListingViewSet, basename="marketplace-listing")

urlpatterns = [
    path("config/", ConfigView.as_view(), name="marketplace-config"),
    path("categories/", CategoryListView.as_view(), name="marketplace-categories"),
    path("tags/", TagListView.as_view(), name="marketplace-tags"),
    path("listings/<slug:slug>/checkout/", CheckoutView.as_view(), name="marketplace-checkout"),
    path("checkout/confirm/", CheckoutConfirmView.as_view(), name="marketplace-checkout-confirm"),
    path("library/", LibraryView.as_view(), name="marketplace-library"),
    path("me/", SalesView.as_view(), name="marketplace-sales"),
    path("me/account/", ConnectedAccountView.as_view(), name="marketplace-account"),
    path("me/account-session/", AccountSessionView.as_view(), name="marketplace-account-session"),
    path("me/payouts/", PayoutView.as_view(), name="marketplace-payouts"),
    path("purchases/<int:pk>/refund/", RefundView.as_view(), name="marketplace-refund"),
    path("", include(router.urls)),
]

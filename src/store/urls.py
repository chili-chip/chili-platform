from django.urls import include, path
from rest_framework.routers import DefaultRouter

from store.views import (
    CheckoutConfirmView,
    CheckoutView,
    OrderViewSet,
    ProductViewSet,
    StripeWebhookView,
)

router = DefaultRouter()
router.register("products", ProductViewSet, basename="store-product")
router.register("orders", OrderViewSet, basename="store-order")

urlpatterns = [
    path("checkout/", CheckoutView.as_view(), name="store-checkout"),
    path("checkout/confirm/", CheckoutConfirmView.as_view(), name="store-checkout-confirm"),
    path("stripe/webhook/", StripeWebhookView.as_view(), name="store-stripe-webhook"),
    path("", include(router.urls)),
]

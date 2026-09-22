from __future__ import annotations

from rest_framework import mixins, permissions, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from store.models import Order, Product
from store.permissions import IsStaffOrReadOnly
from store.serializers import (
    CheckoutConfirmSerializer,
    CheckoutCreateSerializer,
    OrderSerializer,
    ProductSerializer,
)
from store.services import create_order_checkout, handle_stripe_event, sync_checkout_session
from store.stripe import StripeError, verify_webhook_payload


class ProductViewSet(viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    permission_classes = [IsStaffOrReadOnly]
    lookup_field = "slug"

    def get_queryset(self):
        queryset = Product.objects.all()
        user = self.request.user
        if not (user and user.is_authenticated and user.is_staff):
            queryset = queryset.filter(is_active=True)
        return queryset


class OrderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return (
            Order.objects.filter(user=self.request.user)
            .prefetch_related("items", "items__product")
            .select_related("user")
        )


class CheckoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = CheckoutCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            order, session = create_order_checkout(request.user, serializer.validated_data["items"])
        except StripeError as exc:
            return Response(
                {"detail": str(exc)},
                status=exc.status_code or status.HTTP_502_BAD_GATEWAY,
            )
        order = (
            Order.objects.prefetch_related("items", "items__product")
            .select_related("user")
            .get(pk=order.pk)
        )
        return Response(
            {
                "checkout_url": session["url"],
                "session_id": session["id"],
                "order": OrderSerializer(order).data,
            },
            status=status.HTTP_201_CREATED,
        )


class CheckoutConfirmView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = CheckoutConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            order = sync_checkout_session(serializer.validated_data["session_id"], request.user)
        except StripeError as exc:
            return Response(
                {"detail": str(exc)},
                status=exc.status_code or status.HTTP_502_BAD_GATEWAY,
            )
        order = Order.objects.prefetch_related("items", "items__product").get(pk=order.pk)
        return Response(OrderSerializer(order).data)


class StripeWebhookView(APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        from django.conf import settings

        secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")
        signature = request.headers.get("Stripe-Signature", "")
        try:
            event = verify_webhook_payload(request.body, signature, secret)
            handle_stripe_event(event)
        except StripeError as exc:
            return Response(
                {"detail": str(exc)},
                status=exc.status_code or status.HTTP_400_BAD_REQUEST,
            )
        return Response({"received": True})

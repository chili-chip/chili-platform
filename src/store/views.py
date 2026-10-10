from __future__ import annotations

from django.db.models import Count, F, Prefetch, Q
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import EmailVerified
from app.throttles import StoreCheckoutThrottle, StoreRatingThrottle
from store.models import Category, DeliveryOption, Order, Product, ProductRating
from store.permissions import IsStaffOrReadOnly
from store.serializers import (
    CategorySerializer,
    CheckoutConfirmSerializer,
    CheckoutCreateSerializer,
    DeliveryOptionSerializer,
    OrderSerializer,
    ProductDetailSerializer,
    ProductRatingWriteSerializer,
    ProductSerializer,
)
from store.services import (
    create_order_checkout,
    handle_stripe_event,
    hydrate_shipping,
    product_buyer_ids,
    submit_product_rating,
    sync_checkout_session,
    viewer_product_ratings,
    with_rating_stats,
)
from store.stripe import StripeError, verify_webhook_payload

PRODUCT_ORDERINGS = {
    "name": ("name", "id"),
    "-name": ("-name", "-id"),
    "price": ("price_cents", "name"),
    "-price": ("-price_cents", "name"),
    "newest": ("-created_at", "-id"),
    "rating": (F("rating_average").desc(nulls_last=True), "-rating_count", "name"),
}


def _is_staff(request) -> bool:
    user = request.user
    return bool(user and user.is_authenticated and user.is_staff)


def _cents_param(params, name: str) -> int | None:
    raw = (params.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise ValidationError({name: "Use a whole number of cents."}) from None
    if value < 0:
        raise ValidationError({name: "Use a whole number of cents."})
    return value


class CategoryViewSet(viewsets.ModelViewSet):
    serializer_class = CategorySerializer
    permission_classes = [IsStaffOrReadOnly]
    lookup_field = "slug"
    pagination_class = None

    def get_queryset(self):
        visible = Q(products__isnull=False)
        if not _is_staff(self.request):
            visible &= Q(products__is_active=True)
        return Category.objects.annotate(
            product_count=Count("products", filter=visible)
        ).order_by("sort_order", "name")


class DeliveryOptionViewSet(viewsets.ModelViewSet):
    serializer_class = DeliveryOptionSerializer
    permission_classes = [IsStaffOrReadOnly]
    lookup_field = "slug"
    pagination_class = None

    def get_queryset(self):
        queryset = DeliveryOption.objects.all()
        if not _is_staff(self.request):
            queryset = queryset.filter(is_active=True)
        return queryset


class ProductViewSet(viewsets.ModelViewSet):
    """Catalog. The list takes optional filters:

    ``category`` (slug, or ``none`` for uncategorized), ``search`` (name, SKU,
    and copy), ``min_price_cents``, ``max_price_cents``, ``in_stock=true``, and
    ``ordering`` (``name``, ``-name``, ``price``, ``-price``, ``newest``,
    ``rating``).

    Every product carries ``rating_average``, ``rating_count`` and the
    viewer's own ``my_rating``. The detail adds ``reviews``. Signed-in shoppers
    with a verified email rate a product with ``POST <slug>/rating/``.
    """

    serializer_class = ProductSerializer
    permission_classes = [IsStaffOrReadOnly]
    lookup_field = "slug"

    def get_queryset(self):
        queryset = with_rating_stats(
            Product.objects.select_related("category").prefetch_related(
                "images", "delivery_options"
            )
        )
        if not _is_staff(self.request):
            queryset = queryset.filter(is_active=True)
        if self.action == "list":
            queryset = self._filter_list(queryset)
        if self.action in {"retrieve", "rating"}:
            queryset = queryset.prefetch_related(
                Prefetch("ratings", queryset=ProductRating.objects.select_related("user"))
            )
        return queryset

    def get_serializer_class(self):
        if self.action in {"retrieve", "rating"}:
            return ProductDetailSerializer
        return ProductSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["viewer_ratings"] = viewer_product_ratings(self.request.user)
        return context

    def retrieve(self, request, *args, **kwargs):
        product = self.get_object()
        return Response(self._detail(product))

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[permissions.IsAuthenticated, EmailVerified],
        throttle_classes=[StoreRatingThrottle],
    )
    def rating(self, request, slug=None):
        product = self.get_object()
        serializer = ProductRatingWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        submit_product_rating(
            request.user,
            product,
            serializer.validated_data["stars"],
            serializer.validated_data.get("comment", ""),
        )
        product = self.get_queryset().get(pk=product.pk)
        return Response(self._detail(product), status=status.HTTP_201_CREATED)

    def _detail(self, product: Product) -> dict:
        context = self.get_serializer_context()
        context["buyer_ids"] = product_buyer_ids(product)
        return ProductDetailSerializer(product, context=context).data

    def _filter_list(self, queryset):
        params = self.request.query_params

        category = (params.get("category") or "").strip()
        if category == "none":
            queryset = queryset.filter(category__isnull=True)
        elif category:
            queryset = queryset.filter(category__slug=category)

        search = (params.get("search") or "").strip()[:100]
        for term in search.split():
            queryset = queryset.filter(
                Q(name__icontains=term)
                | Q(sku__icontains=term)
                | Q(short_description__icontains=term)
                | Q(long_description__icontains=term)
                | Q(category__name__icontains=term)
            )

        min_price = _cents_param(params, "min_price_cents")
        if min_price is not None:
            queryset = queryset.filter(price_cents__gte=min_price)
        max_price = _cents_param(params, "max_price_cents")
        if max_price is not None:
            queryset = queryset.filter(price_cents__lte=max_price)

        if (params.get("in_stock") or "").lower() in {"1", "true", "yes"}:
            queryset = queryset.filter(stock__gt=0)

        ordering = PRODUCT_ORDERINGS.get(params.get("ordering") or "")
        if ordering:
            queryset = queryset.order_by(*ordering)
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

    def retrieve(self, request, *args, **kwargs):
        order = hydrate_shipping(self.get_object())
        return Response(OrderSerializer(order).data)


class CheckoutView(APIView):
    permission_classes = [permissions.IsAuthenticated, EmailVerified]
    throttle_classes = [StoreCheckoutThrottle]

    def post(self, request):
        serializer = CheckoutCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            order, session = create_order_checkout(
                request.user,
                serializer.validated_data["items"],
                serializer.validated_data.get("delivery_option"),
            )
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
    permission_classes = [permissions.IsAuthenticated, EmailVerified]

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
            from marketplace.services import handle_marketplace_event

            if not handle_marketplace_event(event):
                handle_stripe_event(event)
        except StripeError as exc:
            return Response(
                {"detail": str(exc)},
                status=exc.status_code or status.HTTP_400_BAD_REQUEST,
            )
        return Response({"received": True})

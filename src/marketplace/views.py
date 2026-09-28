from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework import permissions, status, viewsets
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from marketplace.models import Category, ConnectedAccount, Purchase
from marketplace.payments import account_session_params, open_account_session
from marketplace.serializers import (
    CategorySerializer,
    CheckoutConfirmSerializer,
    ConfigSerializer,
    ListingSerializer,
    ListingWriteSerializer,
    PurchaseSerializer,
)
from marketplace.services import (
    account_is_ready,
    attempt_payout,
    claim_or_checkout,
    confirm_checkout,
    create_listing,
    ensure_connected_account,
    library_game_ids,
    listing_queryset,
    owned_game_ids,
    popular_tags,
    public_listings,
    refund_purchase,
    unpublish_or_delete,
    update_listing,
)
from store.stripe import StripeError


def _stripe_error(exc: StripeError) -> Response:
    return Response({"detail": str(exc)}, status=exc.status_code or status.HTTP_502_BAD_GATEWAY)


def _listing_context(request) -> dict:
    user = request.user
    return {
        "request": request,
        "owned_ids": owned_game_ids(user),
        "library_ids": library_game_ids(user),
    }


class ConfigView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        return Response(ConfigSerializer().to_representation(None))


class CategoryListView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        categories = Category.objects.all()
        return Response(CategorySerializer(categories, many=True).data)


class TagListView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        return Response(popular_tags())


class ListingViewSet(viewsets.ModelViewSet):
    serializer_class = ListingSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]
    lookup_field = "slug"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if self.action == "list":
            return public_listings(self.request.query_params)
        user = self.request.user
        queryset = listing_queryset()
        if user.is_authenticated and user.is_staff:
            return queryset
        if user.is_authenticated:
            return queryset.filter(published=True) | queryset.filter(seller=user)
        return queryset.filter(published=True)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["owned_ids"] = owned_game_ids(self.request.user)
        context["library_ids"] = library_game_ids(self.request.user)
        return context

    def create(self, request, *args, **kwargs):
        serializer = ListingWriteSerializer(data=request.data, context={"creating": True})
        serializer.is_valid(raise_exception=True)
        listing = create_listing(request.user, serializer.validated_data)
        listing = listing_queryset().get(pk=listing.pk)
        return Response(
            ListingSerializer(listing, context=_listing_context(request)).data,
            status=status.HTTP_201_CREATED,
        )

    def partial_update(self, request, *args, **kwargs):
        listing = self.get_object()
        if listing.seller_id != request.user.id:
            return Response({"detail": "You can only edit your own listings."}, status=403)
        serializer = ListingWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        update_listing(listing, serializer.validated_data)
        listing = listing_queryset().get(pk=listing.pk)
        return Response(ListingSerializer(listing, context=_listing_context(request)).data)

    def destroy(self, request, *args, **kwargs):
        listing = self.get_object()
        if listing.seller_id != request.user.id and not request.user.is_staff:
            return Response({"detail": "You can only edit your own listings."}, status=403)
        kept = unpublish_or_delete(listing)
        if kept is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        kept = listing_queryset().get(pk=kept.pk)
        return Response(ListingSerializer(kept, context=_listing_context(request)).data)


class CheckoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, slug: str):
        listing = listing_queryset().filter(slug=slug, published=True).first()
        if listing is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            purchase, session = claim_or_checkout(request.user, listing)
        except ImproperlyConfigured as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except StripeError as exc:
            return _stripe_error(exc)
        purchase = Purchase.objects.select_related("seller", "buyer", "game", "listing").get(pk=purchase.pk)
        body = {"purchase": PurchaseSerializer(purchase, context={"request": request}).data}
        if session is None:
            body["free"] = True
            body["checkout_url"] = ""
            body["session_id"] = ""
        else:
            body["free"] = False
            body["checkout_url"] = session.get("url") or ""
            body["session_id"] = session.get("id") or ""
        return Response(body, status=status.HTTP_201_CREATED)


class CheckoutConfirmView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = CheckoutConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            purchase = confirm_checkout(request.user, serializer.validated_data["session_id"])
        except StripeError as exc:
            return _stripe_error(exc)
        purchase = Purchase.objects.select_related("seller", "buyer", "game", "listing").get(pk=purchase.pk)
        return Response(PurchaseSerializer(purchase, context={"request": request}).data)


class LibraryPagination(PageNumberPagination):
    page_size = 20


class LibraryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        purchases = (
            Purchase.objects.filter(buyer=request.user)
            .exclude(status__in=[Purchase.Status.PENDING, Purchase.Status.FAILED, Purchase.Status.CANCELED])
            .select_related("seller", "buyer", "game", "listing", "earning")
            .order_by("-paid_at", "-id")
        )
        paginator = LibraryPagination()
        page = paginator.paginate_queryset(purchases, request, view=self)
        serializer = PurchaseSerializer(page, many=True, context={"request": request})
        return paginator.get_paginated_response(serializer.data)


class SalesView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        payout = attempt_payout(request.user)
        return Response(_sales_payload(request, payout))


class ConnectedAccountView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        account = ConnectedAccount.objects.filter(user=request.user).first()
        if account is None:
            return Response(_account_payload(None))
        try:
            from marketplace.services import refresh_connected_account

            account = refresh_connected_account(account)
        except StripeError as exc:
            return _stripe_error(exc)
        return Response(_account_payload(account))

    def post(self, request):
        try:
            account = ensure_connected_account(request.user)
        except StripeError as exc:
            return _stripe_error(exc)
        return Response(_account_payload(account), status=status.HTTP_201_CREATED)


class AccountSessionView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        account = ConnectedAccount.objects.filter(user=request.user).first()
        if account is None:
            return Response(
                {"detail": "Connect payouts before Chili can send earnings."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            session = open_account_session(account.stripe_account_id)
        except StripeError as exc:
            return _stripe_error(exc)
        secret = session.get("client_secret") or ""
        if not secret:
            return Response(
                {"detail": "Stripe did not return an account session."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        params = account_session_params(account.stripe_account_id)
        return Response(
            {
                "client_secret": secret,
                "publishable_key": getattr(settings, "STRIPE_PUBLISHABLE_KEY", ""),
                "components": sorted(params["components"].keys()),
            }
        )


class PayoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            result = attempt_payout(request.user)
        except StripeError as exc:
            return _stripe_error(exc)
        return Response(result)


class RefundView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, pk: int):
        purchase = Purchase.objects.filter(pk=pk).first()
        if purchase is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            purchase = refund_purchase(purchase)
        except StripeError as exc:
            return _stripe_error(exc)
        purchase = Purchase.objects.select_related("seller", "buyer", "game", "listing", "earning").get(
            pk=purchase.pk
        )
        return Response(PurchaseSerializer(purchase, context={"request": request}).data)


def _account_payload(account) -> dict:
    if account is None:
        return {
            "stripe_account_id": "",
            "transfers_status": "",
            "payouts_status": "",
            "ready": False,
        }
    return {
        "stripe_account_id": account.stripe_account_id,
        "transfers_status": account.transfers_status,
        "payouts_status": account.payouts_status,
        "ready": account_is_ready(account),
    }


def _sales_payload(request, payout: dict) -> dict:
    user = request.user
    listings = listing_queryset().filter(seller=user)
    games = user.games.filter(released=True).order_by("-updated_at", "-id")
    listed = {listing.game_id: listing.slug for listing in listings}
    sales = (
        Purchase.objects.filter(seller=user)
        .exclude(status__in=[Purchase.Status.PENDING, Purchase.Status.FAILED, Purchase.Status.CANCELED])
        .select_related("buyer", "seller", "game", "listing", "earning")
        .order_by("-paid_at", "-id")[:20]
    )
    return {
        "balance": payout["balance"],
        "payout": {
            "transferred": payout["transferred"],
            "amount_cents": payout["amount_cents"],
            "payout_id": payout["payout_id"],
            "blocked_reason": payout["blocked_reason"],
        },
        "account": _account_payload(ConnectedAccount.objects.filter(user=user).first()),
        "listings": ListingSerializer(listings, many=True, context=_listing_context(request)).data,
        "games": [
            {
                "id": game.id,
                "title": game.title,
                "slug": game.slug,
                "listing_slug": listed.get(game.id, ""),
            }
            for game in games
        ],
        "sales": PurchaseSerializer(sales, many=True, context={"request": request}).data,
    }


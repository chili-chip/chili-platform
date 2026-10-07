from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from django.db import transaction
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView as JwtTokenObtainPairView
from rest_framework_simplejwt.views import TokenRefreshView as JwtTokenRefreshView

from accounts.legal import (
    ACCEPTANCE_REQUIRED,
    TERMS_BEFORE_SELLER,
    account_terms_accepted,
    stamp_acceptance,
)
from accounts.avatars import clear_avatar, decode_avatar_data_url, store_avatar
from accounts.mail import (
    MailDeliveryError,
    MailNotConfigured,
    mail_is_configured,
    send_password_reset_email,
    send_verification_email,
    uses_django_mail,
)
from accounts.models import UserSettings
from accounts.serializers import (
    AvatarUploadSerializer,
    EmailSerializer,
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    PublicProfileSerializer,
    RegisterSerializer,
    UidTokenSerializer,
    UserSerializer,
    UserSettingsSerializer,
)
from accounts.tokens import email_verification_token, password_reset_token, user_from_uid
from app.throttles import (
    AccountWriteThrottle,
    AuthRefreshThrottle,
    AuthRegisterThrottle,
    AuthTokenThrottle,
    PasswordChangeThrottle,
)

User = get_user_model()


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = [AuthRegisterThrottle]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                user = serializer.save()
                send_verification_email(user)
        except MailNotConfigured:
            return _mail_not_configured()
        except MailDeliveryError:
            return _mail_failed()
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "user": UserSerializer(user).data,
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            },
            status=status.HTTP_201_CREATED,
        )


class TokenObtainPairView(JwtTokenObtainPairView):
    throttle_classes = [AuthTokenThrottle]


class TokenRefreshView(JwtTokenRefreshView):
    throttle_classes = [AuthRefreshThrottle]


class VerifyEmailView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = UidTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = user_from_uid(serializer.validated_data["uid"])
        token = serializer.validated_data["token"]
        if user is None or not email_verification_token.check_token(user, token):
            return Response(
                {"detail": "This verification link is invalid or has expired."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not user.email_verified:
            user.email_verified = True
            user.save(update_fields=["email_verified"])
        return Response({"detail": "Email verified."})


class ResendVerificationView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if request.user.email_verified:
            return Response({"detail": "Email is already verified."})
        try:
            send_verification_email(request.user)
        except MailNotConfigured:
            return _mail_not_configured()
        except MailDeliveryError:
            return _mail_failed()
        return Response({"detail": "Verification email sent."})


class PasswordResetView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = EmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            if (
                getattr(settings, "ON_WORKERS", False)
                and not uses_django_mail()
                and not mail_is_configured()
            ):
                raise MailNotConfigured("Mail is not configured.")
            user = User.objects.filter(email__iexact=serializer.validated_data["email"]).first()
            if user is not None:
                send_password_reset_email(user)
        except MailNotConfigured:
            return _mail_not_configured()
        except MailDeliveryError:
            return _mail_failed()
        return Response(
            {"detail": "If an account exists for that email, a reset link is on its way."}
        )


class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = user_from_uid(serializer.validated_data["uid"])
        token = serializer.validated_data["token"]
        if user is None or not password_reset_token.check_token(user, token):
            return Response(
                {"detail": "This reset link is invalid or has expired."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        password = serializer.validated_data["password"]
        try:
            validate_password(password, user=user)
        except DjangoValidationError as exc:
            return Response({"password": list(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
        user.set_password(password)
        user.email_verified = True
        user.save(update_fields=["password", "email_verified"])
        return Response({"detail": "Password updated."})


def _mail_not_configured():
    return Response({"detail": "Mail is not configured."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


def _mail_failed():
    return Response({"detail": "Could not send email."}, status=status.HTTP_502_BAD_GATEWAY)


class AcceptLegalTermsView(APIView):
    """Record terms, privacy, or seller terms. Existing timestamps stay put."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        terms = request.data.get("terms") is True
        seller_terms = request.data.get("seller_terms") is True
        if not terms and not seller_terms:
            return Response({"detail": ACCEPTANCE_REQUIRED}, status=status.HTTP_400_BAD_REQUEST)
        user = request.user
        if seller_terms and not terms and not account_terms_accepted(user):
            return Response({"detail": TERMS_BEFORE_SELLER}, status=status.HTTP_400_BAD_REQUEST)
        stamp_acceptance(user, terms=terms, seller_terms=seller_terms)
        return Response(UserSerializer(user).data)


class MeProfileView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AccountWriteThrottle]

    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def put(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=False)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def patch(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class MeSettingsView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AccountWriteThrottle]

    def get(self, request):
        prefs, _ = UserSettings.objects.get_or_create(user=request.user)
        return Response(UserSettingsSerializer(prefs).data)

    def patch(self, request):
        prefs, _ = UserSettings.objects.get_or_create(user=request.user)
        serializer = UserSettingsSerializer(prefs, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class AvatarView(APIView):
    """Upload (POST) or remove (DELETE) the signed-in user's avatar."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AccountWriteThrottle]

    def post(self, request):
        serializer = AvatarUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload, extension = decode_avatar_data_url(serializer.validated_data["image"])
        except DjangoValidationError as exc:
            return Response({"image": list(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
        store_avatar(request.user, payload, extension, request)
        return Response(UserSerializer(request.user).data)

    def delete(self, request):
        clear_avatar(request.user)
        return Response(UserSerializer(request.user).data)


class PasswordChangeView(APIView):
    """Change the password and revoke every other session's refresh tokens."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [PasswordChangeThrottle]

    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={"user": request.user})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password"])
        for token in OutstandingToken.objects.filter(user=request.user):
            BlacklistedToken.objects.get_or_create(token=token)
        refresh = RefreshToken.for_user(request.user)
        return Response(
            {
                "detail": "Password updated.",
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            }
        )


class PublicProfileView(generics.RetrieveAPIView):
    serializer_class = PublicProfileSerializer
    permission_classes = [permissions.AllowAny]
    lookup_field = "username"
    queryset = User.objects.all()

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
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
from accounts.mail import (
    MailDeliveryError,
    MailNotConfigured,
    mail_is_configured,
    send_email_change_email,
    send_password_reset_email,
    send_verification_email,
    uses_django_mail,
)
from accounts.serializers import (
    EmailChangeConfirmSerializer,
    EmailChangeSerializer,
    EmailSerializer,
    PasswordResetConfirmSerializer,
    PublicProfileSerializer,
    RegisterSerializer,
    UidTokenSerializer,
    UserSerializer,
)
from accounts.tokens import (
    email_verification_token,
    password_reset_token,
    read_email_change_token,
    user_from_uid,
)
from app.throttles import (
    AuthRefreshThrottle,
    AuthRegisterThrottle,
    AuthTokenThrottle,
    EmailChangeThrottle,
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


class EmailChangeView(APIView):
    """Start a change: check the password, then mail a link to the new address."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [EmailChangeThrottle]

    def post(self, request):
        serializer = EmailChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        new_email = serializer.validated_data["new_email"].strip()
        if not user.check_password(serializer.validated_data["password"]):
            return Response(
                {"password": ["Password is incorrect."]}, status=status.HTTP_400_BAD_REQUEST
            )
        if new_email.lower() == user.email.lower():
            return Response(
                {"new_email": ["That is already your email."]}, status=status.HTTP_400_BAD_REQUEST
            )
        if User.objects.filter(email__iexact=new_email).exists():
            return Response(
                {"new_email": ["That email is already in use."]}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            send_email_change_email(user, new_email)
        except MailNotConfigured:
            return _mail_not_configured()
        except MailDeliveryError:
            return _mail_failed()
        return Response({"detail": "Confirmation email sent to the new address."})


class EmailChangeConfirmView(APIView):
    """Finish a change from the emailed link. The email stays unchanged until now."""

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = EmailChangeConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invalid = Response(
            {"detail": "This confirmation link is invalid or has expired."},
            status=status.HTTP_400_BAD_REQUEST,
        )
        data = read_email_change_token(serializer.validated_data["token"])
        if data is None:
            return invalid
        user = User.objects.filter(pk=data["uid"]).first()
        if user is None or user.email != data["old"]:
            return invalid
        new_email = data["new"]
        if User.objects.filter(email__iexact=new_email).exclude(pk=user.pk).exists():
            return Response(
                {"detail": "That email is already in use."}, status=status.HTTP_400_BAD_REQUEST
            )
        user.email = new_email
        user.email_verified = True
        user.save(update_fields=["email", "email_verified"])
        return Response({"detail": "Email updated.", "email": user.email})


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


class PublicProfileView(generics.RetrieveAPIView):
    serializer_class = PublicProfileSerializer
    permission_classes = [permissions.AllowAny]
    lookup_field = "username"
    queryset = User.objects.all()

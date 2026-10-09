from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView as JwtTokenObtainPairView
from rest_framework_simplejwt.views import TokenRefreshView as JwtTokenRefreshView

from accounts.avatars import clear_avatar, decode_avatar_data_url, store_avatar
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
from accounts.models import SocialAccount, UserSettings
from accounts.serializers import (
    AvatarUploadSerializer,
    EmailChangeConfirmSerializer,
    EmailChangeSerializer,
    EmailSerializer,
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    PublicProfileSerializer,
    RegisterSerializer,
    SocialAccountSerializer,
    SocialCallbackSerializer,
    SocialStartSerializer,
    UidTokenSerializer,
    UserSerializer,
    UserSettingsSerializer,
)
from accounts.social import (
    PROVIDERS,
    SocialAuthError,
    fetch_profile,
    get_provider,
    link_account,
    make_state,
    read_state,
    sign_in,
)
from accounts.tokens import (
    email_verification_token,
    password_reset_token,
    read_email_change_token,
    user_from_uid,
)
from app.throttles import (
    AccountWriteThrottle,
    AuthRefreshThrottle,
    AuthRegisterThrottle,
    AuthSocialThrottle,
    AuthTokenThrottle,
    EmailChangeThrottle,
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
    return Response({"detail": "Email is temporarily unavailable. Please try again later."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


def _mail_failed():
    return Response({"detail": "We could not send the email. Please try again later."}, status=status.HTTP_502_BAD_GATEWAY)


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


def _social_error(exc: SocialAuthError) -> Response:
    return Response({"detail": exc.detail, "code": exc.code}, status=exc.status)


class SocialProvidersView(APIView):
    """Providers with credentials set. The frontend shows one button for each."""

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        return Response(
            [
                {"provider": provider.name, "label": provider.label}
                for provider in PROVIDERS.values()
                if provider.configured
            ]
        )


class SocialStartView(APIView):
    """Return the provider's authorize URL and the signed state to keep."""

    permission_classes = [permissions.AllowAny]
    throttle_classes = [AuthSocialThrottle]

    def post(self, request, provider):
        serializer = SocialStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        link = serializer.validated_data["link"]
        if link and not request.user.is_authenticated:
            return Response(
                {"detail": "Sign in to connect an account."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        try:
            chosen = get_provider(provider)
        except SocialAuthError as exc:
            return _social_error(exc)
        state = make_state(
            chosen,
            link_user=request.user if link else None,
            accept_terms=serializer.validated_data["accept_terms"],
        )
        return Response(
            {
                "provider": chosen.name,
                "authorize_url": chosen.authorization_url(state),
                "redirect_uri": chosen.redirect_uri(),
                "state": state,
            }
        )


class SocialCallbackView(APIView):
    """Finish sign-in (or connect, when the state says so) from the provider's code."""

    permission_classes = [permissions.AllowAny]
    throttle_classes = [AuthSocialThrottle]

    def post(self, request, provider):
        serializer = SocialCallbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            chosen = get_provider(provider)
            state = read_state(chosen, serializer.validated_data["state"])
            link_user_id = state.get("link")
            if link_user_id is not None and (
                not request.user.is_authenticated or request.user.pk != link_user_id
            ):
                # Checked before the code is spent so the user can retry signed in.
                return Response(
                    {"detail": "Sign in as the account you are connecting to."},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            profile = fetch_profile(chosen, serializer.validated_data["code"])
            if link_user_id is not None:
                link_account(request.user, chosen, profile)
                accounts = SocialAccount.objects.filter(user=request.user)
                return Response(
                    {
                        "linked": True,
                        "social_accounts": SocialAccountSerializer(accounts, many=True).data,
                    }
                )
            user, created = sign_in(chosen, profile, accept_terms=state.get("terms") is True)
        except SocialAuthError as exc:
            return _social_error(exc)
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "user": UserSerializer(user).data,
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "created": created,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class SocialAccountsView(APIView):
    """List the signed-in user's connected providers."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        accounts = SocialAccount.objects.filter(user=request.user)
        return Response(SocialAccountSerializer(accounts, many=True).data)


class SocialAccountDetailView(APIView):
    """Disconnect a provider. Refused when it is the only way left to sign in."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AccountWriteThrottle]

    def delete(self, request, provider):
        account = SocialAccount.objects.filter(user=request.user, provider=provider).first()
        if account is None:
            return Response(
                {"detail": "That provider is not connected."}, status=status.HTTP_404_NOT_FOUND
            )
        others = SocialAccount.objects.filter(user=request.user).exclude(pk=account.pk)
        if not request.user.has_usable_password() and not others.exists():
            return Response(
                {
                    "detail": "Set a password before disconnecting your only sign-in method.",
                    "code": "last_login_method",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        account.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

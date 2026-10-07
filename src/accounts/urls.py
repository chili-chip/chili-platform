from django.urls import path
from rest_framework_simplejwt.views import TokenBlacklistView

from accounts.views import (
    AcceptLegalTermsView,
    AvatarView,
    MeSettingsView,
    PasswordChangeView,
    MeProfileView,
    PasswordResetConfirmView,
    PasswordResetView,
    PublicProfileView,
    RegisterView,
    ResendVerificationView,
    TokenObtainPairView,
    TokenRefreshView,
    VerifyEmailView,
)

urlpatterns = [
    path("auth/register/", RegisterView.as_view(), name="auth-register"),
    path("auth/token/", TokenObtainPairView.as_view(), name="auth-token"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="auth-token-refresh"),
    path("auth/logout/", TokenBlacklistView.as_view(), name="auth-logout"),
    path("auth/verify-email/", VerifyEmailView.as_view(), name="auth-verify-email"),
    path(
        "auth/verify-email/resend/",
        ResendVerificationView.as_view(),
        name="auth-verify-email-resend",
    ),
    path("auth/password/reset/", PasswordResetView.as_view(), name="auth-password-reset"),
    path(
        "auth/password/reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="auth-password-reset-confirm",
    ),
    path("auth/password/change/", PasswordChangeView.as_view(), name="auth-password-change"),
    path("profiles/me/avatar/", AvatarView.as_view(), name="profile-avatar"),
    path("profiles/me/settings/", MeSettingsView.as_view(), name="profile-settings"),
    path("profiles/me/", MeProfileView.as_view(), name="profile-me"),
    path("profiles/me/acceptance/", AcceptLegalTermsView.as_view(), name="profile-acceptance"),
    path("profiles/<str:username>/", PublicProfileView.as_view(), name="profile-detail"),
]

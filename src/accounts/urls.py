from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.views import MeProfileView, PublicProfileView, RegisterView

urlpatterns = [
    path("auth/register/", RegisterView.as_view(), name="auth-register"),
    path("auth/token/", TokenObtainPairView.as_view(), name="auth-token"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="auth-token-refresh"),
    path("profiles/me/", MeProfileView.as_view(), name="profile-me"),
    path("profiles/<str:username>/", PublicProfileView.as_view(), name="profile-detail"),
]

from __future__ import annotations

from rest_framework import permissions


class EmailVerified(permissions.BasePermission):
    """Reject authenticated writes until the account email is verified.

    Anonymous requests and safe methods are left to the other permission
    classes, so login and public reads still work.
    """

    message = "Verify your email before doing that."

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        user = request.user
        if not user or not user.is_authenticated:
            return True
        return bool(getattr(user, "email_verified", False))

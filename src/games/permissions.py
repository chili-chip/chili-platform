from rest_framework import permissions


class IsOwnerOrReadOnly(permissions.BasePermission):
    """Released games are public. Only the owner may change, release, or delete one."""

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        user = request.user
        return bool(user and user.is_authenticated and obj.owner_id == user.id)

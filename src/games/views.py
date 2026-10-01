from __future__ import annotations

from django.db.models import Q
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.permissions import EmailVerified
from games.models import Game
from games.permissions import IsOwnerOrReadOnly
from games.serializers import GameSerializer
from marketplace.services import library_game_ids

_PROJECTS = {"0", "false", "no"}


class GameViewSet(viewsets.ModelViewSet):
    serializer_class = GameSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, EmailVerified, IsOwnerOrReadOnly]
    queryset = Game.objects.select_related("owner", "listing")
    lookup_value_regex = r"\d+"

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if self.action != "list":
            if user.is_authenticated:
                return queryset.filter(Q(released=True) | Q(owner=user))
            return queryset.filter(released=True)

        username = self.request.query_params.get("username")
        if username:
            queryset = queryset.filter(owner__username=username)
        wants_projects = self.request.query_params.get("released", "true").lower() in _PROJECTS
        if wants_projects:
            if not user.is_authenticated:
                return queryset.none()
            if username and username != user.username:
                return queryset.none()
            if not username:
                queryset = queryset.filter(owner=user)
            return queryset.filter(released=False)
        return queryset.filter(released=True)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["detail"] = self.action in {"retrieve", "update", "partial_update", "create", "release"}
        context["library_ids"] = library_game_ids(self.request.user)
        return context

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(detail=True, methods=["post"], url_path="release")
    def release(self, request, pk=None):
        game = self.get_object()
        if not game.released:
            game.released = True
            game.save(update_fields=["released", "updated_at"])
        return Response(self.get_serializer(game).data, status=status.HTTP_200_OK)

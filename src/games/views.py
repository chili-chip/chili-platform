from __future__ import annotations

from rest_framework import permissions, viewsets

from games.models import Game
from games.permissions import IsOwnerOrReadOnly
from games.serializers import GameSerializer


class GameViewSet(viewsets.ModelViewSet):
    serializer_class = GameSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsOwnerOrReadOnly]
    queryset = Game.objects.select_related("owner")
    lookup_value_regex = r"\d+"

    def get_queryset(self):
        queryset = super().get_queryset()
        username = self.request.query_params.get("username")
        if username:
            queryset = queryset.filter(owner__username=username)
        return queryset

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

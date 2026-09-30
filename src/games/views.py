from __future__ import annotations

from django.db.models import Q
from django.http import StreamingHttpResponse
from rest_framework import permissions, renderers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from games.assistant import (
    MODEL_FAILED,
    PROJECT_TOO_LARGE,
    AssistantError,
    AssistantUnavailable,
    ProjectTooLarge,
    assistant_unavailable_detail,
    encode_event,
    iter_assist_events,
    prepare_messages,
    run_workers_ai,
)
from games.models import Game
from games.permissions import IsOwnerOrReadOnly
from games.serializers import AssistRequestSerializer, GameSerializer
from marketplace.services import library_game_ids

_PROJECTS = {"0", "false", "no"}


class NdjsonRenderer(renderers.JSONRenderer):
    """Lets the browser ask for the assist stream without a 406.

    Error bodies are still one JSON object. A merged game is a
    StreamingHttpResponse and does not go through this renderer.
    """

    media_type = "application/x-ndjson"
    format = "ndjson"


class GameViewSet(viewsets.ModelViewSet):
    serializer_class = GameSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsOwnerOrReadOnly]
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

    @action(
        detail=True,
        methods=["post"],
        url_path="assist",
        renderer_classes=[renderers.JSONRenderer, NdjsonRenderer],
    )
    def assist(self, request, pk=None):
        """Ask Workers AI to change one piece of the Bitsy text on screen.

        The body `data` is the editor document. This does not write the game.
        The response is newline-delimited JSON: reply text first, then the
        merged document or an error.
        """
        self.get_object()
        serializer = AssistRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        game_data = serializer.validated_data["data"]
        try:
            messages = prepare_messages(
                game_data,
                message=serializer.validated_data["message"],
                history=serializer.validated_data.get("history") or [],
            )
            outcome = run_workers_ai(messages)
        except ProjectTooLarge:
            return Response({"detail": PROJECT_TOO_LARGE}, status=status.HTTP_400_BAD_REQUEST)
        except AssistantUnavailable:
            return Response(
                {"detail": assistant_unavailable_detail()},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except AssistantError:
            return Response({"detail": MODEL_FAILED}, status=status.HTTP_502_BAD_GATEWAY)

        def generate():
            for event in iter_assist_events(game_data, outcome):
                yield encode_event(event)

        response = StreamingHttpResponse(generate(), content_type="application/x-ndjson")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

from __future__ import annotations

from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


def api_exception_handler(exc, context):
    """Keep API errors as JSON even when DEBUG would otherwise render HTML."""
    response = drf_exception_handler(exc, context)
    if response is not None:
        return response
    return Response(
        {"detail": "Something went wrong. Try again in a moment."},
        status=500,
    )

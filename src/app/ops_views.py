"""Secret-gated management endpoints for Workers/D1 bootstrapping."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST


def _authorized(request) -> bool:
    expected = getattr(settings, "OPS_TOKEN", "")
    provided = request.headers.get("X-Ops-Token", "")
    return bool(expected) and provided == expected


def _ensure_admin() -> dict:
    username = getattr(settings, "ADMIN_USERNAME", "admin")
    email = getattr(settings, "ADMIN_EMAIL", "admin@localhost")
    password = getattr(settings, "ADMIN_PASSWORD", "")
    if not password:
        return {"username": username, "created": False, "skipped": True}

    User = get_user_model()
    user = User.objects.filter(username=username).first()
    created = user is None
    if created:
        user = User(username=username, email=email)
    user.email = email
    user.is_staff = True
    user.is_superuser = True
    user.set_password(password)
    user.save()
    return {"username": username, "created": created, "skipped": False}


@csrf_exempt
@require_POST
def migrate_view(request):
    if not _authorized(request):
        return JsonResponse({"detail": "Unauthorized."}, status=401)
    call_command("migrate", interactive=False, verbosity=1)
    return JsonResponse({"status": "migrated"})


@csrf_exempt
@require_POST
def seed_view(request):
    if not _authorized(request):
        return JsonResponse({"detail": "Unauthorized."}, status=401)
    call_command("seed_forum", verbosity=1)
    return JsonResponse({"status": "seeded"})


@csrf_exempt
@require_POST
def bootstrap_view(request):
    if not _authorized(request):
        return JsonResponse({"detail": "Unauthorized."}, status=401)
    call_command("migrate", interactive=False, verbosity=1)
    call_command("seed_forum", verbosity=1)
    admin = _ensure_admin()
    return JsonResponse({"status": "bootstrapped", "admin": admin})

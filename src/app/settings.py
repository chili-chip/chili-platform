"""Chili Platform Django settings.

Local `manage.py` uses SQLite. Cloudflare Workers use D1 via django-cf
and R2 for uploaded media.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from app.hashlib_compat import install as install_pbkdf2

BASE_DIR = Path(__file__).resolve().parent.parent

# WorkerEntrypoint.fetch is async; django-cf's D1 ORM is sync.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")
install_pbkdf2()


def _env(name: str, default: str = "") -> str:
    """Read a Worker binding/secret first, then process environment."""
    try:
        from workers import env as worker_env

        value = getattr(worker_env, name, None)
        if value not in (None, ""):
            return str(value)
    except Exception:
        pass
    return os.environ.get(name, default)


def _running_on_workers() -> bool:
    if os.getenv("WORKERS_CI") == "1":
        return False
    try:
        from workers import env as worker_env

        return getattr(worker_env, "DB", None) is not None
    except Exception:
        return False


SECRET_KEY = _env("DJANGO_SECRET_KEY", "chili-platform-dev-secret-change-me")
DEBUG = _env("DJANGO_DEBUG", "true").lower() in {"1", "true", "yes"}
ALLOWED_HOSTS = [
    host.strip()
    for host in _env("DJANGO_ALLOWED_HOSTS", "*").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt",
    "accounts",
    "community",
    "store",
    "marketplace",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "app.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "app.wsgi.application"
ASGI_APPLICATION = "app.asgi.application"

# Workers CI has no sqlite3 module; skip DB during collectstatic.
if os.getenv("WORKERS_CI") == "1":
    DATABASES: dict = {}
elif _running_on_workers():
    DATABASES = {
        "default": {
            "ENGINE": "django_cf.db.backends.d1",
            "CLOUDFLARE_BINDING": "DB",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Workers hashlib has SHA-256 but not pbkdf2_hmac. Keep PBKDF2 installed so
# hashes created outside the Worker can still be verified after a polyfill.
PASSWORD_HASHERS = [
    "app.hashers.SaltedSHA256PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR.parent / "staticfiles" / "static"
MEDIA_URL = _env("MEDIA_URL", "/media/")
MEDIA_ROOT = BASE_DIR / "media"

if _running_on_workers():
    STORAGES = {
        "default": {
            "BACKEND": "django_cf.storage.R2Storage",
            "OPTIONS": {
                "binding": "ASSETS_BUCKET",
                "location": "media",
                "allow_overwrite": False,
            },
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
else:
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in _env(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:4200,http://127.0.0.1:4200",
    ).split(",")
    if origin.strip()
]
CORS_ALLOW_CREDENTIALS = True

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticatedOrReadOnly",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=14),
    "ROTATE_REFRESH_TOKENS": False,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

OPS_TOKEN = _env("OPS_TOKEN", "chili-dev-ops-token")
ADMIN_USERNAME = _env("ADMIN_USERNAME", "admin")
ADMIN_EMAIL = _env("ADMIN_EMAIL", "admin@localhost")
ADMIN_PASSWORD = _env("ADMIN_PASSWORD", "")

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

_SETTINGS_DEV_SECRET_KEY = "chili-platform-dev-secret-change-me"
_SETTINGS_DEV_OPS_TOKEN = "chili-dev-ops-token"

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


def _optional_int(name: str) -> int | None:
    """Integer setting with no default. Empty means the operator has not set it."""
    raw = _env(name, "").strip()
    if raw == "":
        return None
    return int(raw)


def _running_on_workers() -> bool:
    """True on the deployed Worker Python runtime (Pyodide), not local manage.py."""
    if os.getenv("WORKERS_CI") == "1":
        return False
    if os.getenv("CHILI_LOCAL_DJANGO") == "1":
        return False
    try:
        import pyodide  # noqa: F401
    except ImportError:
        return False
    return True


SECRET_KEY = _env("DJANGO_SECRET_KEY", _SETTINGS_DEV_SECRET_KEY)
DEBUG = _env("DJANGO_DEBUG", "true").lower() in {"1", "true", "yes"}
ALLOWED_HOSTS = [
    host.strip()
    for host in _env("DJANGO_ALLOWED_HOSTS", "*").split(",")
    if host.strip()
]

_on_workers = _running_on_workers()

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
    "rest_framework_simplejwt.token_blacklist",
    "accounts",
    "community",
    "store",
    "games",
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
elif _on_workers:
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

# New passwords use Worker PBKDF2 (hashlib_compat on the Worker). The salted
# SHA-256 hasher stays so older hashes still verify and can be upgraded.
PASSWORD_HASHERS = [
    "app.hashers.WorkerPBKDF2PasswordHasher",
    "app.hashers.SaltedSHA256PasswordHasher",
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR.parent / "staticfiles" / "static"
MEDIA_URL = _env("MEDIA_URL", "/media/")
MEDIA_ROOT = BASE_DIR / "media"
PUBLIC_BASE_URL = _env("PUBLIC_BASE_URL", "http://localhost:8787")
DATA_UPLOAD_MAX_MEMORY_SIZE = 8 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 8 * 1024 * 1024

if _on_workers:
    STORAGES = {
        "default": {
            "BACKEND": "django_cf.storage.R2Storage",
            "OPTIONS": {
                "binding": "ASSETS_BUCKET",
                "location": "",
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
    "EXCEPTION_HANDLER": "app.exceptions.api_exception_handler",
    # No DEFAULT_THROTTLE_CLASSES: authenticated reads stay open.
    # Public write views opt in. Rates are per IP for auth and per user after login.
    # The Bitsy editor autosaves about 700ms after an edit, so game writes are
    # per minute. Two tabs at that pace are about 170 saves a minute.
    "DEFAULT_THROTTLE_RATES": {
        "auth_register": "30/hour",
        "auth_token": "10/minute",
        "auth_refresh": "30/minute",
        "forum_write": "60/hour",
        "game_write": "240/minute",
        "listing_write": "30/hour",
        "store_checkout": "10/hour",
        "marketplace_checkout": "10/hour",
    },
}

TEST_RUNNER = "app.test_runner.CacheResetRunner"

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=14),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# Pages origin used in verification and password-reset links.
FRONTEND_BASE_URL = _env("FRONTEND_BASE_URL", "http://localhost:4200").rstrip("/")
GMAIL_CLIENT_ID = _env("GMAIL_CLIENT_ID", "")
GMAIL_CLIENT_SECRET = _env("GMAIL_CLIENT_SECRET", "")
GMAIL_REFRESH_TOKEN = _env("GMAIL_REFRESH_TOKEN", "")
GMAIL_SENDER = _env("GMAIL_SENDER", "")
DEFAULT_FROM_EMAIL = GMAIL_SENDER or "chili@localhost"

# wrangler dev loads `.dev.vars` (not used in production). An explicit backend
# wins even on Workers. `manage.py` is not on Workers, so it prints to stdout.
# Leaving this unset on a Worker keeps Django's SMTP default, which the mail
# client treats as "send with the Gmail API".
_email_backend = _env("EMAIL_BACKEND", "").strip()
if _email_backend:
    EMAIL_BACKEND = _email_backend
elif not _on_workers:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

OPS_TOKEN = _env("OPS_TOKEN", _SETTINGS_DEV_OPS_TOKEN)
ADMIN_USERNAME = _env("ADMIN_USERNAME", "admin")
ADMIN_EMAIL = _env("ADMIN_EMAIL", "admin@localhost")
ADMIN_PASSWORD = _env("ADMIN_PASSWORD", "")
ON_WORKERS = _on_workers

STRIPE_SECRET_KEY = _env("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = _env("STRIPE_WEBHOOK_SECRET", "")
STRIPE_WEBHOOK_TOLERANCE = int(_env("STRIPE_WEBHOOK_TOLERANCE", "300"))
STRIPE_SYNC_ENABLED = _env("STRIPE_SYNC_ENABLED", "true").lower() in {"1", "true", "yes"}
STORE_CURRENCY = _env("STORE_CURRENCY", "eur").lower()
STORE_CHECKOUT_SUCCESS_URL = _env(
    "STORE_CHECKOUT_SUCCESS_URL",
    "http://localhost:4200/store/checkout/success?session_id={CHECKOUT_SESSION_ID}",
)
STORE_CHECKOUT_CANCEL_URL = _env(
    "STORE_CHECKOUT_CANCEL_URL",
    "http://localhost:4200/store/checkout/cancel",
)
STORE_INTEGRATION_IDENTIFIER = _env(
    "STORE_INTEGRATION_IDENTIFIER",
    "chili-store-hwchkout",
)
STORE_SHIPPING_COUNTRIES = [
    country.strip().upper()
    for country in _env(
        "STORE_SHIPPING_COUNTRIES",
        "AT,BE,BG,HR,CY,CZ,DK,EE,FI,FR,DE,GR,HU,IE,IT,LV,LT,LU,MT,NL,PL,PT,RO,SK,SI,ES,SE",
    ).split(",")
    if country.strip()
]

# Game sales: separate charges and transfers. Chili keeps 20% plus an
# operator-set estimate of card processing by transferring less.
# MARKETPLACE_PROCESSING_FEE_BPS and MARKETPLACE_PROCESSING_FEE_FIXED_CENTS
# have no default. Set both from https://stripe.com/pricing for the charge
# currency and method. Rates vary by region, card, and method.
STRIPE_PUBLISHABLE_KEY = _env("STRIPE_PUBLISHABLE_KEY", "")
MARKETPLACE_CURRENCY = _env("MARKETPLACE_CURRENCY", "eur").lower()
MARKETPLACE_PLATFORM_FEE_BPS = int(_env("MARKETPLACE_PLATFORM_FEE_BPS", "2000"))
MARKETPLACE_PROCESSING_FEE_BPS = _optional_int("MARKETPLACE_PROCESSING_FEE_BPS")
MARKETPLACE_PROCESSING_FEE_FIXED_CENTS = _optional_int("MARKETPLACE_PROCESSING_FEE_FIXED_CENTS")
MARKETPLACE_MIN_PAID_CENTS = int(_env("MARKETPLACE_MIN_PAID_CENTS", "100"))
MARKETPLACE_MIN_PAYOUT_CENTS = int(_env("MARKETPLACE_MIN_PAYOUT_CENTS", "2000"))
MARKETPLACE_HOLD_DAYS = int(_env("MARKETPLACE_HOLD_DAYS", "7"))
MARKETPLACE_INTEGRATION_IDENTIFIER = _env("MARKETPLACE_INTEGRATION_IDENTIFIER", "chili-mkt-kprwqmzn")
MARKETPLACE_CHECKOUT_SUCCESS_URL = _env(
    "MARKETPLACE_CHECKOUT_SUCCESS_URL",
    "http://localhost:4200/marketplace/checkout/success?session_id={CHECKOUT_SESSION_ID}",
)
MARKETPLACE_CHECKOUT_CANCEL_URL = _env(
    "MARKETPLACE_CHECKOUT_CANCEL_URL",
    "http://localhost:4200/marketplace/checkout/cancel",
)


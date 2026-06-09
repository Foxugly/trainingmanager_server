from datetime import timedelta
from pathlib import Path

import environ
from django.conf import settings

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env", overwrite=True)
SECRET_KEY = env("SECRET_KEY")

# C5: no defaults for DEBUG / ALLOWED_HOSTS — same fail-loud pattern as
# SECRET_KEY. A missing env var raises ImproperlyConfigured at import time
# instead of silently falling into a permissive prod-unsafe value
# (DEBUG=True, ALLOWED_HOSTS=['*']). dev.py / dev fixtures override these
# explicitly for local convenience; prod.py expects them in the .env.
DEBUG = env.bool("DEBUG")
STATE = env("STATE", default="INT")
WEBSITE = env("WEBSITE", default="www.example.com")

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")

INSTALLED_APPS = [
    "modeltranslation",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "debug_toolbar",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "drf_spectacular",
    "django_filters",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "sport",
    "ai",
    "aiusage",
    "program",
    "event",
    "round",
    "exercise",
    "member",
    "customuser",
    "team",
    "note",
    "attendance",
    "roti",
    "rsvp",
    "level",
    "notifications",
    "messaging",
    "attachment",
    "performance",
    "audit",
    "place",
    "equipment",
    "dashboard",
]

SITE_ID = 1

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "tools.middleware.UserLanguageMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "django-trainingmanager.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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

WSGI_APPLICATION = "django-trainingmanager.wsgi.application"

# Database — fleet DB_* 6-var convention (OPERATIONS.md §3.13). Prod reads the
# six bare DB_* names from SSM → /run/tm/.env; the engine alias accepts the
# short "postgresql"/"postgres". When DB_ENGINE is unset (local dev / CI /
# pytest) we fall back to DATABASE_URL (default sqlite) — the ical-style override.
_DB_ENGINE_ALIASES = {
    "sqlite3": "django.db.backends.sqlite3",
    "postgresql": "django.db.backends.postgresql",
    "postgres": "django.db.backends.postgresql",
}
if env("DB_ENGINE", default=""):
    DATABASES = {
        "default": {
            "ENGINE": _DB_ENGINE_ALIASES.get(env("DB_ENGINE"), env("DB_ENGINE")),
            "NAME": env("DB_NAME"),
            "USER": env("DB_USER"),
            "PASSWORD": env("DB_PASSWORD"),
            "HOST": env("DB_HOST"),
            "PORT": env("DB_PORT"),
        }
    }
else:
    DATABASES = {
        "default": env.db("DATABASE_URL", default="sqlite:///db.sqlite3"),
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

LANGUAGES = [
    ("fr", "Français"),
    ("nl", "Nederlands"),
    ("en", "English"),
    ("it", "Italiano"),
    ("es", "Español"),
]

LOCALE_PATHS = [
    BASE_DIR / "locale",
]

MODELTRANSLATION_DEFAULT_LANGUAGE = "fr"
MODELTRANSLATION_LANGUAGES = ("fr", "nl", "en", "it", "es")
MODELTRANSLATION_FALLBACK_LANGUAGES = {"default": ("fr",)}

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

AUTH_USER_MODEL = "customuser.CustomUser"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

DEBUG_TOOLBAR_CONFIG = {
    "SHOW_TOOLBAR_CALLBACK": lambda request: settings.DEBUG,
}

# -------------------------- LOGGING --------------------------------
# Console is always on (so dev/CI see output). A rotating file handler
# is added when DEBUG=False so prod has a persistent log without burdening
# dev / pytest with disk writes (and without hardcoding a Linux path that
# would crash on Windows). The log directory is created on demand,
# strictly when needed; pytest runs with DEBUG=True so it never touches
# the filesystem here.
LOG_DIR = BASE_DIR / "logs"
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} [{levelname}] {name}: {message}",
            "style": "{",
        },
        "json": {
            "()": "tools.logging_json.JsonFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
            "level": "DEBUG" if DEBUG else "INFO",
        },
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "team": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "customuser": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "event": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "program": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "tools": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

if not DEBUG:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # File handler uses the JSON formatter so an observability stack
    # (Datadog / GCP Cloud Logging / ELK) can index records without a
    # parser. Console keeps the human-readable verbose format.
    LOGGING["handlers"]["file"] = {
        "class": "logging.handlers.RotatingFileHandler",
        "filename": str(LOG_DIR / "django.log"),
        "maxBytes": 10 * 1024 * 1024,
        "backupCount": 5,
        "formatter": "json",
        "level": "INFO",
    }
    for name in LOGGING["loggers"]:
        LOGGING["loggers"][name]["handlers"].append("file")

# CORS allowed origins are environment-driven so prod can lock down to its
# real frontend origin(s) without a code change. Default is empty to
# fail-closed in any environment that forgets to set it; dev.py keeps a
# hardcoded local override for the Angular dev server convenience.
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

# Allow the Sentry distributed-tracing headers on cross-origin API calls. The
# frontend SDK (browserTracingIntegration with tracePropagationTargets → tm-api)
# attaches `sentry-trace` + `baggage` to every request; without them in the
# allowlist the browser CORS-preflight-blocks EVERY call (login/register/…).
from corsheaders.defaults import default_headers  # noqa: E402

CORS_ALLOW_HEADERS = (*default_headers, "sentry-trace", "baggage")

# Number of trusted reverse proxies in front of the app (nginx = 1). DRF's
# throttles and our get_remote_ip take the client IP at this depth from the END
# of X-Forwarded-For, so a client-spoofed leading XFF entry can't defeat per-IP
# throttles / Turnstile remoteip. Set higher if a CDN is added in front.
TRUSTED_PROXY_COUNT = env.int("TRUSTED_PROXY_COUNT", default=1)

REST_FRAMEWORK = {
    "NUM_PROXIES": TRUSTED_PROXY_COUNT,
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "tools.pagination.StandardPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    # Global baseline throttle for unauthenticated callers (per IP). Endpoints
    # with their own throttle_classes override this (e.g. ai_* + auth_* below).
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "100/hour",
        "ai_ping": "30/hour",
        "ai_plan_generation": "10/hour",
        "ai_training_generation": "10/hour",
        # Anonymous auth-flow throttles (per IP)
        "auth_register": "5/hour",
        "auth_resend_email": "3/hour",
        "auth_login": "10/min",
        "auth_password_reset": "3/hour",
        "auth_magic_link_request": "5/hour",
        # Authenticated auth-flow throttles (per user)
        "auth_logout": "30/min",
    },
    "EXCEPTION_HANDLER": "tools.exceptions.custom_exception_handler",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    # Blacklist enabled: rotated refresh tokens are blacklisted, and
    # POST /auth/logout/ blacklists the caller's refresh on demand.
    # Requires the `rest_framework_simplejwt.token_blacklist` app
    # (declared above in INSTALLED_APPS).
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    # Custom key (not consumed by simplejwt itself) — read by
    # VerifiedTokenObtainPairSerializer when the client sets remember=True.
    "REFRESH_TOKEN_LIFETIME_REMEMBER": timedelta(days=30),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Training Manager API",
    "DESCRIPTION": "API pour la gestion d entrainements",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Split request vs response schemas: request bodies (create/update/patch) get
    # their own *Request components WITHOUT read-only fields, so the typed client's
    # write payloads no longer need `as unknown as <Model>` casts to satisfy
    # readonly-required fields.
    "COMPONENT_SPLIT_REQUEST": True,
    "ENUM_NAME_OVERRIDES": {
        "JoinRequestStatusEnum": "team.models.TeamJoinRequest.STATUS_CHOICES",
        "InvitationStatusEnum": "team.models.TeamInvitation.STATUS_CHOICES",
        "OverlapStrategyEnum": "program.choices.OVERLAP_STRATEGY_CHOICES",
        "VisibilityMode": "event.models.VisibilityMode",
        "RsvpStatusEnum": "rsvp.models.RsvpStatus",
        "UnitEnum": "performance.models.Unit",
        "TrainingTypeEnum": "tools.choices.TrainingType",
    },
}

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

ACCOUNT_LOGIN_METHODS = {"username", "email"}
ACCOUNT_SIGNUP_FIELDS = ["username*", "email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_USER_MODEL_USERNAME_FIELD = "username"
ACCOUNT_USER_MODEL_EMAIL_FIELD = "email"

# Custom adapter routes the email confirmation link to the frontend
# (replaces the role HEADLESS_FRONTEND_URLS used to play before we
# disabled allauth.headless).
ACCOUNT_ADAPTER = "customuser.adapter.FrontendAccountAdapter"

# Public SPA base URL (fleet OPERATIONS.md §3.14). FRONTEND_URL stays as an
# in-code alias — adapter / magic_action / team views still reference it.
FRONTEND_BASE_URL = env("FRONTEND_BASE_URL")
FRONTEND_URL = FRONTEND_BASE_URL

GRAPH_TENANT_ID = env("GRAPH_TENANT_ID")
GRAPH_CLIENT_ID = env("GRAPH_CLIENT_ID")
GRAPH_CLIENT_SECRET = env("GRAPH_CLIENT_SECRET")
GRAPH_SENDER = env("GRAPH_SENDER")

EMAIL_BACKEND = "tools.email.GraphEmailBackend"
DEFAULT_FROM_EMAIL = env("GRAPH_SENDER")

# Cloudflare Turnstile (captcha on /auth/register/).
# TURNSTILE_SITE_KEY is the public widget key, safe to commit and expose
# to the frontend. TURNSTILE_SECRET_KEY is the server-side verification
# secret and MUST stay in .env (never committed).
TURNSTILE_SITE_KEY = env("TURNSTILE_SITE_KEY", default="0x4AAAAAADI-0tcntdflxEqO")
TURNSTILE_SECRET_KEY = env("TURNSTILE_SECRET_KEY", default="")

ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default="")
ANTHROPIC_MODEL_DEFAULT = env("ANTHROPIC_MODEL_DEFAULT", default="claude-haiku-4-5-20251001")
ANTHROPIC_MAX_TOKENS_DEFAULT = env.int("ANTHROPIC_MAX_TOKENS_DEFAULT", default=2048)
# Plan generation produces a long JSON list of events (one per session over
# many weeks); a 41-week × 3-per-week plan needs ~12k output tokens. The
# default 2048 is far too small here. Other AI features (training generation,
# ping) keep the smaller default.
ANTHROPIC_MAX_TOKENS_PLAN = env.int("ANTHROPIC_MAX_TOKENS_PLAN", default=20000)
ANTHROPIC_TIMEOUT_SECONDS = env.int("ANTHROPIC_TIMEOUT_SECONDS", default=60)

# --- File attachments (S3 presigned uploads/downloads) ---------------------
# The browser uploads/downloads files DIRECTLY to/from a private S3 bucket via
# short-lived presigned URLs the backend mints with the EC2 instance role's
# credentials (boto3 default credential chain — NO keys in code/settings).
# Access is gated by app permissions at presign / download time.
#
# ATTACHMENTS_S3_BUCKET is published to SSM (/tm/prod/ATTACHMENTS_S3_BUCKET) by
# deploy/create-attachments-infra.sh; an empty value means the feature is
# unconfigured and the mutating endpoints fail-closed with HTTP 503.
ATTACHMENTS_S3_BUCKET = env("ATTACHMENTS_S3_BUCKET", default="")
ATTACHMENTS_S3_REGION = env("ATTACHMENTS_S3_REGION", default="eu-west-1")
ATTACHMENTS_MAX_BYTES = env.int("ATTACHMENTS_MAX_BYTES", default=500 * 1024 * 1024)  # 500 MB
ATTACHMENTS_PRESIGN_EXPIRY = env.int("ATTACHMENTS_PRESIGN_EXPIRY", default=3600)  # seconds

# Allow-list of MIME types accepted for upload: PDF, common images, common
# video. Overridable via env (comma-separated) so prod can widen/narrow it
# without a code change. frozenset = immutable shared default.
_DEFAULT_ATTACHMENTS_ALLOWED_MIME = (
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "video/mp4",
    "video/webm",
    "video/quicktime",
)
ATTACHMENTS_ALLOWED_MIME = frozenset(
    env.list("ATTACHMENTS_ALLOWED_MIME", default=list(_DEFAULT_ATTACHMENTS_ALLOWED_MIME))
)

# --- Sentry (error tracking / performance) — OPERATIONS.md §3.8 -------------
# Only active under prod (STATE=PROD): even with a DSN present in dev/test we
# never send DEV/INT/TEST events (they would pollute the dashboard). The SDK
# auto-enables its Django integration when importable, so a bare init suffices.
# DSN + STATE come from SSM /tm/prod/*.
_SENTRY_PROD_ACTIVE = STATE.strip().upper() == "PROD"
SENTRY_DSN = env("SENTRY_DSN", default="")
if SENTRY_DSN and _SENTRY_PROD_ACTIVE:
    import sentry_sdk
    from django.core.exceptions import DisallowedHost

    def _sentry_before_send(event, hint):
        # Drop public-endpoint noise: bots hitting the API with a bad Host header
        # raise DisallowedHost (a handled 400, not a real bug).
        exc = (hint.get("exc_info") or (None, None, None))[1]
        if isinstance(exc, DisallowedHost):
            return None
        if str(event.get("logger", "")).startswith("django.security.DisallowedHost"):
            return None
        return event

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=STATE,
        release=env("SENTRY_RELEASE", default=""),
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.0),
        profiles_sample_rate=env.float("SENTRY_PROFILES_SAMPLE_RATE", default=0.0),
        send_default_pii=env.bool("SENTRY_SEND_PII", default=False),
        before_send=_sentry_before_send,
    )

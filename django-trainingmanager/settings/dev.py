from .base import *  # noqa: F401, F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

# SSL redirect / HSTS / Secure cookies are deliberately NOT set here:
# they live in prod.py only, gated on DEBUG=False. Activating them locally
# would break http://localhost previews (HSTS pin), runserver (SSL_REDIRECT
# loops), and admin login (Secure cookies refuse the insecure origin).

# django-debug-toolbar is a dev-only app: loaded here, never in prod.
INSTALLED_APPS = [*INSTALLED_APPS, "debug_toolbar"]  # noqa: F405
INTERNAL_IPS = ["127.0.0.1"]
if "debug_toolbar.middleware.DebugToolbarMiddleware" not in MIDDLEWARE:
    MIDDLEWARE = ["debug_toolbar.middleware.DebugToolbarMiddleware"] + list(MIDDLEWARE)

CORS_ALLOWED_ORIGINS = [
    "http://localhost:4200",
    "http://127.0.0.1:4200",
]

# Django 4.0+ enforces an Origin check on unsafe methods (POST/PUT/...)
# when a same-origin token alone isn't enough — typically when the request
# arrives with a non-matching Origin/Referer header (HSTS-upgraded HTTPS,
# Vite/Angular dev-proxy at :4200 forwarding to /admin/, etc.). On a fresh
# DEBUG runserver this manifests as 'Origin checking failed - X does not
# match any trusted origins' on the admin login POST. Listing the local
# dev origins explicitly fixes /admin/ in every realistic dev setup.
CSRF_TRUSTED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:4200",
    "http://127.0.0.1:4200",
]

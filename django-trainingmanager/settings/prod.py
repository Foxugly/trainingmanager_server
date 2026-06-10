from django.utils.csp import CSP

from .base import *  # noqa: F401, F403

DEBUG = False

# ---------------------- SSL / TLS ------------------------------------
# Redirect every HTTP hit to HTTPS at the Django layer; the reverse proxy
# in front (nginx / Caddy / a load balancer) is expected to terminate TLS
# and forward the original scheme via the X-Forwarded-Proto header so
# Django can detect the secure origin.
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ---------------------- HSTS -----------------------------------------
# 1 year, includeSubDomains, preload-eligible. Once a browser has seen
# this header on the apex domain it will refuse plain HTTP for the whole
# subdomain tree until the TTL expires — which is why this is gated on
# DEBUG=False (i.e. prod settings only): activating it locally would make
# any HTTP localhost preview break.
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# ---------------------- Cookies / CSRF -------------------------------
# Cookies are only sent over HTTPS, never readable from JS, with a
# Lax SameSite (the standard balance — protects against CSRF while
# keeping top-level navigation flows working). HttpOnly on the CSRF
# cookie too: Django reads it server-side, not from JS — DRF's
# JWT-authenticated endpoints don't need a CSRF token at all, and the
# admin reads the value from the request, not from JS.
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"

X_FRAME_OPTIONS = "DENY"

# Explicit Referrer-Policy: don't depend on Django's evolving default.
# 'same-origin' = the Referer header is sent on same-origin requests
# (so server-side analytics & logs keep their context) but stripped on
# cross-origin navigation, which is the safer default for an admin /
# API surface that doesn't want to leak its URLs to third-party sites.
SECURE_REFERRER_POLICY = "same-origin"

# CORS_ALLOWED_ORIGINS is now read from the environment in base.py — no
# code change needed at deploy time, set CORS_ALLOWED_ORIGINS in the
# prod .env (comma-separated, e.g.
#   CORS_ALLOWED_ORIGINS=https://app.example.com,https://admin.example.com
# ).

# ---------------------- Content-Security-Policy ----------------------
# Native Django 6 CSP (django.middleware.csp), enforced on every Django HTML
# response: the admin, the DRF browsable API, error pages and the
# server-rendered RSVP magic page (rsvp.magic_views, which additionally sets a
# *stricter* per-page policy via @csp_override).
#
# This is NOT the SPA's CSP. The Angular app is served by nginx as static
# files, so the policy that protects end users against XSS in the app lives in
# the nginx vhost (next phase: Content-Security-Policy-Report-Only first,
# observe, then enforce).
#
# script-src / style-src keep 'unsafe-inline' because the Django admin and the
# DRF browsable API ship non-nonced inline assets; tightening those (e.g. by
# disabling the browsable API in prod and switching to nonces) is deferred to
# the report-only-then-enforce pass. The remaining directives are real,
# non-breaking hardening that no Django-served page depends on:
#   - object-src 'none'      no <object>/<embed> plugins
#   - base-uri 'self'        block <base> tag hijacking
#   - form-action 'self'     forms may only POST to this origin
#   - frame-ancestors 'none' anti-clickjacking, reinforces X_FRAME_OPTIONS=DENY
SECURE_CSP = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF, CSP.UNSAFE_INLINE],
    "style-src": [CSP.SELF, CSP.UNSAFE_INLINE],
    "img-src": [CSP.SELF, "data:"],
    "font-src": [CSP.SELF, "data:"],
    "connect-src": [CSP.SELF],
    "object-src": [CSP.NONE],
    "base-uri": [CSP.SELF],
    "form-action": [CSP.SELF],
    "frame-ancestors": [CSP.NONE],
}

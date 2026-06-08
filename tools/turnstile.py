"""Cloudflare Turnstile server-side verification.

Frontend renders the Turnstile widget with TURNSTILE_SITE_KEY (public),
captures the user-completed token, and POSTs it alongside the registration
payload. The backend then exchanges that token with Cloudflare via this
helper before honouring the registration.

Fail-closed: any timeout, network error, or non-success Cloudflare
response returns False. There is no fallback that would let a request
through without a verified token.
"""

import logging

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT_SECONDS = 5.0


def verify_turnstile_token(token: str, remote_ip: str | None = None) -> bool:
    """Exchange the client-side Turnstile token for a verification result.

    Returns True only when Cloudflare reports {"success": true}. Any other
    outcome — empty token, network failure, timeout, malformed response,
    explicit failure — returns False.
    """
    if not token:
        return False

    secret = getattr(settings, "TURNSTILE_SECRET_KEY", None)
    if not secret:
        logger.error("TURNSTILE_SECRET_KEY is not configured; refusing to verify token.")
        return False

    payload = {"secret": secret, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        response = httpx.post(VERIFY_URL, data=payload, timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError:
        logger.exception("Turnstile siteverify network error; treating as failure.")
        return False

    if response.status_code != 200:
        logger.warning("Turnstile siteverify returned %s: %s", response.status_code, response.text)
        return False

    try:
        data = response.json()
    except ValueError:
        logger.warning("Turnstile siteverify returned non-JSON body: %r", response.text)
        return False

    return bool(data.get("success"))


def get_remote_ip(request) -> str | None:
    """Client IP, honouring exactly ``settings.TRUSTED_PROXY_COUNT`` trusted
    reverse proxies. We take the entry that many positions from the END of
    X-Forwarded-For (the address the trusted proxy actually saw), NOT the first
    (client-controllable) entry — so a spoofed leading XFF can't forge the IP
    used for Turnstile remoteip / throttling."""
    from django.conf import settings

    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            depth = max(1, getattr(settings, "TRUSTED_PROXY_COUNT", 1))
            idx = min(depth, len(parts))
            return parts[-idx]
    return request.META.get("REMOTE_ADDR")

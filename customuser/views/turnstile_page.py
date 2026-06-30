"""WebView-hosted Cloudflare Turnstile widget for the mobile app.

The mobile app has no native Turnstile SDK, so register / forgot-password load
this page in a WebView. It renders the *dedicated mobile* widget
(``TURNSTILE_SITE_KEY_MOBILE``; falls back to the web key in dev) and, on
success, hands the token back to the app through a JS bridge:

  - Android: a ``@JavascriptInterface`` object injected as ``TMTurnstileBridge``
    → ``TMTurnstileBridge.onToken(token)`` / ``TMTurnstileBridge.onError()``
  - iOS (WKWebView): ``window.webkit.messageHandlers.turnstile.postMessage(token)``
    on success, and ``window.webkit.messageHandlers.turnstileError.postMessage()``
    on widget error/expiry (parity with Android's ``TMTurnstileBridge.onError()``)

Served at ``/turnstile/`` (root, not ``/api/``) on tm-api.foxugly.com — the
widget's Cloudflare hostname allowlist is pinned to that domain, and nginx
proxies ``/`` to gunicorn so no extra nginx route is needed. A strict per-page
CSP (overriding the global policy) admits only challenges.cloudflare.com.
"""

from django.conf import settings
from django.http import HttpResponse
from django.middleware.csp import get_nonce
from django.utils.csp import CSP
from django.utils.html import escape
from django.views.decorators.csp import csp_override
from django.views.decorators.http import require_http_methods

# Turnstile pulls its script + challenge iframe from challenges.cloudflare.com.
# Everything else is denied; our inline <style>/<script> carry the CSP nonce.
_TURNSTILE_CSP = {
    "default-src": [CSP.NONE],
    "script-src": [CSP.NONCE, "https://challenges.cloudflare.com"],
    "frame-src": ["https://challenges.cloudflare.com"],
    "style-src": [CSP.NONCE],
    "connect-src": ["https://challenges.cloudflare.com"],
    "img-src": [CSP.SELF, "data:"],
    "base-uri": [CSP.NONE],
    "frame-ancestors": [CSP.NONE],
}


@csp_override(_TURNSTILE_CSP)
@require_http_methods(["GET"])
def turnstile_page(request) -> HttpResponse:
    """Render the Turnstile host page. GET only; no auth (public widget page)."""
    nonce = escape(str(get_nonce(request)))
    site_key = escape(
        getattr(settings, "TURNSTILE_SITE_KEY_MOBILE", "") or settings.TURNSTILE_SITE_KEY
    )
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Verification</title>
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
<style nonce="{nonce}">
  body {{ font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
         background: #f3f4f6; color: #111827; margin: 0;
         display: flex; align-items: center; justify-content: center; min-height: 100vh; }}
  .wrap {{ text-align: center; padding: 1rem; }}
  .muted {{ color: #6b7280; font-size: .9rem; margin-top: 1rem; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="cf-turnstile" data-sitekey="{site_key}"
       data-callback="onToken" data-error-callback="onError" data-expired-callback="onExpired"></div>
  <p class="muted">Verifying you are human…</p>
</div>
<script nonce="{nonce}">
  function deliver(token) {{
    try {{
      if (window.TMTurnstileBridge && window.TMTurnstileBridge.onToken) {{
        window.TMTurnstileBridge.onToken(token); return;
      }}
      if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.turnstile) {{
        window.webkit.messageHandlers.turnstile.postMessage(token); return;
      }}
    }} catch (e) {{}}
  }}
  function onToken(token) {{ deliver(token); }}
  function onError() {{
    try {{ if (window.TMTurnstileBridge && window.TMTurnstileBridge.onError) window.TMTurnstileBridge.onError(); }} catch (e) {{}}
    try {{
      if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.turnstileError) {{
        window.webkit.messageHandlers.turnstileError.postMessage("error");
      }}
    }} catch (e) {{}}
    try {{ if (window.turnstile) turnstile.reset(); }} catch (e) {{}}
  }}
  function onExpired() {{ try {{ if (window.turnstile) turnstile.reset(); }} catch (e) {{}} }}
</script>
</body>
</html>"""
    return HttpResponse(html, content_type="text/html; charset=utf-8")

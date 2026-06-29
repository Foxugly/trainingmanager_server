"""Dual-secret Turnstile verification (web + mobile) and the WebView host page."""

from unittest.mock import patch

import pytest
from django.test import override_settings

from tools.turnstile import verify_turnstile_token


def _fake_post(success_for_secret):
    """httpx.post stub: {"success": true} only when payload secret matches."""

    def post(url, data=None, timeout=None):
        class _Resp:
            status_code = 200
            text = ""

            def json(self):
                return {"success": data.get("secret") == success_for_secret}

        return _Resp()

    return post


@override_settings(TURNSTILE_SECRET_KEY="web-secret", TURNSTILE_SECRET_KEY_MOBILE="mobile-secret")
def test_accepts_mobile_token_when_only_mobile_secret_matches():
    with patch("tools.turnstile.httpx.post", side_effect=_fake_post("mobile-secret")):
        assert verify_turnstile_token("tok") is True


@override_settings(TURNSTILE_SECRET_KEY="web-secret", TURNSTILE_SECRET_KEY_MOBILE="mobile-secret")
def test_accepts_web_token_when_only_web_secret_matches():
    with patch("tools.turnstile.httpx.post", side_effect=_fake_post("web-secret")):
        assert verify_turnstile_token("tok") is True


@override_settings(TURNSTILE_SECRET_KEY="web-secret", TURNSTILE_SECRET_KEY_MOBILE="mobile-secret")
def test_rejects_when_neither_secret_matches():
    with patch("tools.turnstile.httpx.post", side_effect=_fake_post("nope")):
        assert verify_turnstile_token("tok") is False


def test_empty_token_is_rejected_without_network():
    with patch("tools.turnstile.httpx.post") as post:
        assert verify_turnstile_token("") is False
        post.assert_not_called()


@override_settings(TURNSTILE_SECRET_KEY="", TURNSTILE_SECRET_KEY_MOBILE="")
def test_no_secret_configured_is_rejected_without_network():
    with patch("tools.turnstile.httpx.post") as post:
        assert verify_turnstile_token("tok") is False
        post.assert_not_called()


@pytest.mark.django_db
def test_turnstile_page_renders_with_mobile_sitekey_and_csp(client):
    with override_settings(TURNSTILE_SITE_KEY_MOBILE="0xMOBILEKEY"):
        resp = client.get("/turnstile/")
    assert resp.status_code == 200
    assert b"0xMOBILEKEY" in resp.content
    assert b"challenges.cloudflare.com" in resp.content
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "challenges.cloudflare.com" in csp


@pytest.mark.django_db
def test_turnstile_page_falls_back_to_web_sitekey_in_dev(client):
    with override_settings(TURNSTILE_SITE_KEY_MOBILE="", TURNSTILE_SITE_KEY="0xWEBKEY"):
        resp = client.get("/turnstile/")
    assert resp.status_code == 200
    assert b"0xWEBKEY" in resp.content

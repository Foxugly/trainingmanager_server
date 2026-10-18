"""HttpOnly refresh cookie for the web SPA (opt-in via ``X-Auth-Mode: cookie``).

Fleet design piloted on PushIT (PushIT_server#69): the SPA sends the header
on its auth calls only; the refresh token then travels in an HttpOnly cookie
scoped to ``/api/v1/auth/`` and is omitted from the JSON body. Without the
header (the KMP app ``trainingmanager_app``, old SPA bundles) the body-token
contract is unchanged. See CLAUDE.md "Refresh token transport".
"""

import uuid

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from customuser.email_tokens import make_confirmation_key, make_key
from customuser.magic_link_token import make_magic_link_token
from member.models import Member
from team.models import TeamInvitation, TeamMembership

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_allowed_origin")]

User = get_user_model()

LOGIN_URL = "/api/v1/auth/token/"
REFRESH_URL = "/api/v1/auth/token/refresh/"
LOGOUT_URL = "/api/v1/auth/logout/"
PASSWORD_CHANGE_URL = "/api/v1/auth/password/change/"
PASSWORD_RESET_CONFIRM_URL = "/api/v1/auth/password/reset/confirm/"
EMAIL_CONFIRM_URL = "/api/v1/auth/email/confirm/"
MAGIC_EXCHANGE_URL = "/api/v1/auth/magic-link/exchange/"
ACCOUNT_DELETE_URL = "/api/v1/auth/account/delete/"

ORIGIN = "https://tm.foxugly.com"
EMAIL = "cookie@local.test"
PASSWORD = "S0meP@ssw0rd!"
COOKIE = "tm_refresh"
DAY = 24 * 3600


@pytest.fixture
def _allowed_origin():
    with override_settings(CORS_ALLOWED_ORIGINS=[ORIGIN]):
        yield


@pytest.fixture
def user():
    return User.objects.create_user(email=EMAIL, password=PASSWORD, email_confirmed=True)


def spa_client(origin=ORIGIN, **extra):
    """A browser-like client: cookie opt-in header + Origin on every request."""
    headers = {"HTTP_X_AUTH_MODE": "cookie", **extra}
    if origin is not None:
        headers["HTTP_ORIGIN"] = origin
    client = APIClient()
    client.credentials(**headers)
    return client


def login(client, **extra):
    return client.post(LOGIN_URL, {"email": EMAIL, "password": PASSWORD, **extra}, format="json")


def is_blacklisted(token: str) -> bool:
    try:
        RefreshToken(token)
    except Exception:
        return True
    return False


def assert_cookie_max_age(cookie, days):
    assert abs(int(cookie["max-age"]) - days * DAY) <= 5


# --- login: cookie set / absent ---------------------------------------------


def test_login_in_cookie_mode_sets_httponly_cookie_and_omits_refresh(user):
    resp = login(spa_client())

    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert "refresh" not in body
    assert body["access"]
    cookie = resp.cookies[COOKIE]
    assert cookie.value
    assert cookie["httponly"]
    assert cookie["secure"]
    assert cookie["samesite"] == "Lax"
    assert cookie["path"] == "/api/v1/auth/"
    assert cookie["domain"] == ""  # host-only on the API
    assert_cookie_max_age(cookie, 7)


def test_login_remember_in_cookie_mode_gives_a_30_day_cookie(user):
    resp = login(spa_client(), remember=True)

    assert resp.status_code == 200
    assert_cookie_max_age(resp.cookies[COOKIE], 30)


def test_login_without_header_keeps_body_tokens_and_sets_no_cookie(user):
    resp = login(APIClient())

    assert resp.status_code == 200
    assert resp.json()["refresh"]
    assert COOKIE not in resp.cookies


def test_failed_login_in_cookie_mode_sets_no_cookie(user):
    resp = spa_client().post(LOGIN_URL, {"email": EMAIL, "password": "wrong"}, format="json")

    assert resp.status_code == 401
    assert COOKIE not in resp.cookies


# --- other issuing endpoints ------------------------------------------------


def test_magic_link_exchange_in_cookie_mode_sets_cookie(user):
    nonce = uuid.uuid4().hex
    user.magic_link_nonce = nonce
    user.save(update_fields=["magic_link_nonce"])

    resp = spa_client().post(
        MAGIC_EXCHANGE_URL, {"token": make_magic_link_token(user.id, nonce)}, format="json"
    )

    assert resp.status_code == 200, resp.content
    assert "refresh" not in resp.json()
    assert resp.cookies[COOKIE].value


def test_email_confirm_in_cookie_mode_sets_cookie():
    pending = User.objects.create_user(email="new@local.test", password=PASSWORD)

    resp = spa_client().post(
        EMAIL_CONFIRM_URL, {"key": make_confirmation_key(pending)}, format="json"
    )

    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert "refresh" not in body
    assert body["access"] and body["user"]["email"] == "new@local.test"
    assert resp.cookies[COOKIE].value


def test_password_reset_confirm_in_cookie_mode_sets_cookie(user):
    resp = spa_client().post(
        PASSWORD_RESET_CONFIRM_URL,
        {"key": make_key(user), "new_password": "BrandN3wP@ss!"},
        format="json",
    )

    assert resp.status_code == 200, resp.content
    assert "refresh" not in resp.json()
    assert resp.cookies[COOKIE].value


def test_invitation_completion_in_cookie_mode_sets_cookie(trainer_user):
    team = trainer_user.owned_teams.first()
    member = Member.objects.create(firstname="Co", lastname="Okie", email="inv@local.test")
    TeamMembership.objects.create(team=team, member=member)
    inv = TeamInvitation.objects.create(team=team, member=member, email="inv@local.test")

    resp = spa_client().post(
        f"/api/v1/invitations/lookup/{inv.token}/", {"password": "StrongPass!2026"}, format="json"
    )

    assert resp.status_code == 201, resp.content
    assert "refresh" not in resp.json()
    cookie = resp.cookies[COOKIE]
    assert cookie.value
    assert cookie["path"] == "/api/v1/auth/"


# --- refresh through the cookie ---------------------------------------------


def test_refresh_reads_the_cookie_and_rotates_it(user):
    client = spa_client()
    original = login(client).cookies[COOKIE].value

    resp = client.post(REFRESH_URL, {}, format="json")

    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["access"]
    assert "refresh" not in body
    rotated = resp.cookies[COOKIE].value
    assert rotated and rotated != original
    assert is_blacklisted(original)
    assert_cookie_max_age(resp.cookies[COOKIE], 7)


def test_replaying_a_rotated_cookie_is_rejected(user):
    client = spa_client()
    original = login(client).cookies[COOKIE].value
    client.post(REFRESH_URL, {}, format="json")

    replay = spa_client()
    replay.cookies[COOKIE] = original
    resp = replay.post(REFRESH_URL, {}, format="json")

    assert resp.status_code == 401


def test_refresh_without_cookie_in_cookie_mode_is_401(user):
    resp = spa_client().post(REFRESH_URL, {}, format="json")

    assert resp.status_code == 401
    assert resp.json()["code"] == "refresh_cookie_missing"


def test_body_token_in_cookie_mode_migrates_to_the_cookie(user):
    """One-time migration: an SPA still holding a refresh in localStorage."""
    legacy = login(APIClient()).json()["refresh"]

    resp = spa_client().post(REFRESH_URL, {"refresh": legacy}, format="json")

    assert resp.status_code == 200, resp.content
    assert "refresh" not in resp.json()
    assert resp.cookies[COOKIE].value
    assert is_blacklisted(legacy)


# --- Origin check (CSRF) ----------------------------------------------------


@pytest.mark.parametrize("origin", ["https://evil.example", None])
def test_cookie_refresh_from_a_foreign_or_missing_origin_is_403(user, origin):
    cookie = login(spa_client()).cookies[COOKIE].value

    attacker = spa_client(origin=origin)
    attacker.cookies[COOKIE] = cookie
    resp = attacker.post(REFRESH_URL, {}, format="json")

    assert resp.status_code == 403
    assert resp.json()["code"] == "origin_not_allowed"
    assert not is_blacklisted(cookie)


def test_cookie_logout_from_a_foreign_origin_is_403(user):
    cookie = login(spa_client()).cookies[COOKIE].value

    attacker = spa_client(origin="https://evil.example")
    attacker.cookies[COOKIE] = cookie
    resp = attacker.post(LOGOUT_URL, {}, format="json")

    assert resp.status_code == 403
    assert not is_blacklisted(cookie)


def test_cookie_login_from_a_foreign_origin_is_403(user):
    resp = login(spa_client(origin="https://evil.example"))

    assert resp.status_code == 403
    assert resp.json()["code"] == "origin_not_allowed"
    assert COOKIE not in resp.cookies


# --- logout ------------------------------------------------------------------


def test_logout_blacklists_the_cookie_and_clears_it_without_bearer(user):
    client = spa_client()
    cookie = login(client).cookies[COOKIE].value

    resp = client.post(LOGOUT_URL, {}, format="json")

    assert resp.status_code == 204, resp.content
    cleared = resp.cookies[COOKIE]
    assert cleared.value == ""
    assert cleared["path"] == "/api/v1/auth/"
    assert int(cleared["max-age"]) == 0
    assert is_blacklisted(cookie)
    assert client.post(REFRESH_URL, {}, format="json").status_code == 401


def test_logout_in_cookie_mode_is_idempotent_without_a_cookie(user):
    resp = spa_client().post(LOGOUT_URL, {}, format="json")

    assert resp.status_code == 204
    assert resp.cookies[COOKIE].value == ""


def test_logout_in_cookie_mode_does_not_revoke_someone_elses_body_token(user):
    """With a Bearer, the ownership check of body mode still applies."""
    other = User.objects.create_user(email="other@local.test", password=PASSWORD)
    victim = str(RefreshToken.for_user(other))
    access = login(spa_client()).json()["access"]

    resp = spa_client(HTTP_AUTHORIZATION=f"Bearer {access}").post(
        LOGOUT_URL, {"refresh": victim}, format="json"
    )

    assert resp.status_code == 204
    assert not is_blacklisted(victim)


# --- password change / account deletion ---------------------------------------


def test_password_change_in_cookie_mode_resets_the_cookie(user):
    client = spa_client()
    login_resp = login(client)
    old_cookie = login_resp.cookies[COOKIE].value
    client.credentials(
        HTTP_X_AUTH_MODE="cookie",
        HTTP_ORIGIN=ORIGIN,
        HTTP_AUTHORIZATION=f"Bearer {login_resp.json()['access']}",
    )

    resp = client.post(
        PASSWORD_CHANGE_URL,
        {"current_password": PASSWORD, "new_password": "UnAutreMotDePasse456!"},
        format="json",
    )

    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert "refresh" not in body
    assert body["access"]
    new_cookie = resp.cookies[COOKIE].value
    assert new_cookie and new_cookie != old_cookie
    assert is_blacklisted(old_cookie)
    assert not is_blacklisted(new_cookie)


def test_account_deletion_in_cookie_mode_clears_the_cookie(user):
    client = spa_client()
    access = login(client).json()["access"]
    client.credentials(
        HTTP_X_AUTH_MODE="cookie", HTTP_ORIGIN=ORIGIN, HTTP_AUTHORIZATION=f"Bearer {access}"
    )

    resp = client.post(ACCOUNT_DELETE_URL, {"current_password": PASSWORD}, format="json")

    assert resp.status_code == 204, resp.content
    assert resp.cookies[COOKIE].value == ""


# --- mobile app: body flow unchanged ----------------------------------------


def test_app_body_flow_is_unchanged(user):
    app = APIClient()
    refresh = login(app).json()["refresh"]

    refreshed = app.post(REFRESH_URL, {"refresh": refresh}, format="json")
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh"] != refresh
    assert COOKIE not in refreshed.cookies

    # Body mode still requires the refresh field (400, not a cookie lookup).
    assert app.post(REFRESH_URL, {}, format="json").status_code == 400

    # Body-mode logout still needs a Bearer token...
    new_refresh = refreshed.json()["refresh"]
    assert app.post(LOGOUT_URL, {"refresh": new_refresh}, format="json").status_code == 401
    app.credentials(HTTP_AUTHORIZATION=f"Bearer {refreshed.json()['access']}")
    # ...and the refresh in the body.
    assert app.post(LOGOUT_URL, {}, format="json").status_code == 400
    resp = app.post(LOGOUT_URL, {"refresh": new_refresh}, format="json")
    assert resp.status_code == 204
    assert COOKIE not in resp.cookies


# --- CORS ---------------------------------------------------------------------


def test_cors_preflight_allows_the_opt_in_header_with_credentials():
    resp = APIClient().options(
        REFRESH_URL,
        HTTP_ORIGIN=ORIGIN,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type,x-auth-mode",
    )

    assert resp["Access-Control-Allow-Origin"] == ORIGIN
    assert resp["Access-Control-Allow-Credentials"] == "true"
    assert "x-auth-mode" in resp["Access-Control-Allow-Headers"]


def test_cors_preflight_from_a_foreign_origin_gets_no_allow_origin():
    resp = APIClient().options(
        REFRESH_URL,
        HTTP_ORIGIN="https://evil.example",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="x-auth-mode",
    )

    assert "Access-Control-Allow-Origin" not in resp

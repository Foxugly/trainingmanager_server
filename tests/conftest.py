import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tests.factories import TeamFactory


@pytest.fixture(autouse=True)
def use_locmem_email_backend(settings):
    """Block real Graph email sends across the test suite."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"


@pytest.fixture(autouse=True)
def reset_throttle_cache():
    """DRF throttle counters live in the default cache (locmem in tests).

    The cache persists across tests in the same process, so without an
    explicit clear, throttle counters from one test leak into the next
    and silently 429 unrelated tests.
    """
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def authenticated_user(db):
    User = get_user_model()
    user = User.objects.create_user(
        email="testuser@local.test",
        password="Str0ngP@ssTest!",
    )
    return user


@pytest.fixture
def auth_client(api_client, authenticated_user):
    api_client.force_authenticate(user=authenticated_user)
    return api_client


@pytest.fixture
def user_team(authenticated_user):
    return TeamFactory(owner=authenticated_user)


@pytest.fixture
def trainer_user(db):
    User = get_user_model()
    user = User.objects.create_user(
        email="trainer@local.test",
        password="Str0ngP@ssTrainer!",
    )
    TeamFactory(owner=user, is_active=True)
    return user


@pytest.fixture
def trainer_sport(trainer_user):
    """Sport of the trainer's first team — useful for catalog scoping tests."""
    return trainer_user.owned_teams.first().sport


@pytest.fixture
def auth_client_trainer(api_client, trainer_user):
    api_client.force_authenticate(user=trainer_user)
    return api_client


@pytest.fixture
def non_trainer_user(db):
    User = get_user_model()
    return User.objects.create_user(
        email="spectator@local.test",
        password="Str0ngP@ssSpec!",
    )


@pytest.fixture
def auth_client_non_trainer(api_client, non_trainer_user):
    api_client.force_authenticate(user=non_trainer_user)
    return api_client


@pytest.fixture
def admin_user(db):
    User = get_user_model()
    return User.objects.create_user(
        email="adminuser@local.test",
        password="Str0ngP@ssAdmin!",
        is_staff=True,
    )


@pytest.fixture
def admin_client(api_client, admin_user):
    api_client.force_authenticate(user=admin_user)
    return api_client


@pytest.fixture
def set_throttle_rate(monkeypatch):
    """Override DRF Throttle.THROTTLE_RATES for the duration of a test.

    Direct monkey-patching is required because SimpleRateThrottle.THROTTLE_RATES
    is captured as a class attribute at import time; pytest-django's settings
    fixture only replaces the live settings dict and does not propagate.

    Both UserRateThrottle (AI endpoints, JWT-authenticated) and
    AnonRateThrottle (auth flow endpoints, anonymous) read from this dict,
    so we patch both class attributes."""
    from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

    new_rates = dict(UserRateThrottle.THROTTLE_RATES)

    def _set(scope, rate):
        new_rates[scope] = rate

    monkeypatch.setattr(UserRateThrottle, "THROTTLE_RATES", new_rates)
    monkeypatch.setattr(AnonRateThrottle, "THROTTLE_RATES", new_rates)
    return _set

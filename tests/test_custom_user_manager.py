"""Coverage of CustomUserManager.create_user / create_superuser.

The custom manager mirrors Django's standard UserManager: it accepts
arbitrary **extra_fields (first_name, last_name, is_staff, language,
...), normalises the email domain, and validates required arguments.
"""

import pytest
from django.contrib.auth import get_user_model

User = get_user_model()

pytestmark = pytest.mark.django_db


def test_create_user_with_extra_fields():
    """create_user accepts first_name, last_name, language, is_staff via kwargs."""
    user = User.objects.create_user(
        email="alice@example.com",
        password="testpass123",
        first_name="Alice",
        last_name="Wonder",
        language="nl",
        is_staff=True,
    )
    assert user.email == "alice@example.com"
    assert user.first_name == "Alice"
    assert user.last_name == "Wonder"
    assert user.language == "nl"
    assert user.is_staff is True
    assert user.is_superuser is False
    assert user.check_password("testpass123")


def test_create_user_normalizes_email():
    """create_user lowercases the email domain (BaseUserManager.normalize_email)."""
    user = User.objects.create_user(
        email="Bob@Example.COM",
        password="testpass123",
    )
    assert user.email == "Bob@example.com"


def test_create_user_defaults():
    """Without extra_fields: is_active=True, is_staff=False, is_superuser=False."""
    user = User.objects.create_user(
        email="charlie@example.com",
        password="testpass123",
    )
    assert user.is_active is True
    assert user.is_staff is False
    assert user.is_superuser is False


def test_create_user_requires_email():
    """Email-only: create_user raises ValueError when email is missing/blank
    (it is the USERNAME_FIELD now; there is no username argument)."""
    with pytest.raises(ValueError):
        User.objects.create_user(email="", password="p")
    with pytest.raises(ValueError):
        User.objects.create_user(email=None, password="p")


def test_create_superuser_sets_flags():
    """create_superuser auto-sets is_staff=True and is_superuser=True."""
    su = User.objects.create_superuser(
        email="admin2@example.com",
        password="testpass123",
    )
    assert su.is_staff is True
    assert su.is_superuser is True
    assert su.is_active is True


def test_create_superuser_rejects_non_staff():
    """create_superuser raises ValueError if is_staff=False is passed explicitly."""
    with pytest.raises(ValueError):
        User.objects.create_superuser(
            email="bad@example.com",
            password="p",
            is_staff=False,
        )

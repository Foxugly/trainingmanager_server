"""Coverage of subscription_bypass — accès offert accordé sans souscription.

- Le champ existe, défaut False, avec note d'audit et date d'octroi.
- Il est DISTINCT de is_staff, qui n'accorde aucun droit métier
  (cf. tests/test_team_quota.py, Decision I (b) — doit rester vert).
"""

import pytest
from django.contrib.auth import get_user_model

pytestmark = pytest.mark.django_db

User = get_user_model()


def _user(name, **kwargs):
    return User.objects.create_user(email=f"{name}@local.test", password="Sup3rS@fePass!", **kwargs)


def test_subscription_bypass_defaults_to_false():
    user = _user("bypass_default")
    assert user.subscription_bypass is False
    assert user.bypass_note == ""
    assert user.bypass_granted_at is None

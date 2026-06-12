"""Coverage of /api/v1/attendance-statuses/.

Pattern: catalog referential (read auth, write staff). Same shape as
/api/v1/sports/.
"""

import pytest
from django.contrib.auth import get_user_model

from attendance.models import AttendanceStatus

pytestmark = pytest.mark.django_db


User = get_user_model()


@pytest.fixture
def staff_user(db):
    return User.objects.create_user(
        email="as_staff@local.test",
        password="pass",
        is_staff=True,
    )


@pytest.fixture
def staff_client(api_client, staff_user):
    api_client.force_authenticate(user=staff_user)
    return api_client


URL = "/api/v1/attendance-statuses/"


def test_GET_list_unauthenticated_returns_401(api_client):
    response = api_client.get(URL)
    assert response.status_code == 401


def test_GET_list_authenticated_returns_3_default(auth_client):
    response = auth_client.get(URL)
    assert response.status_code == 200
    body = response.json()
    codes = {row["code"] for row in body["results"]}
    assert {"present", "absent", "excused"}.issubset(codes)


def test_GET_list_returns_localized_label_fr(auth_client):
    """Public flavor exposes localized 'label' — French via Accept-Language."""
    response = auth_client.get(URL, HTTP_ACCEPT_LANGUAGE="fr")
    assert response.status_code == 200
    by_code = {row["code"]: row for row in response.json()["results"]}
    assert by_code["present"]["label"] == "Présent"
    assert by_code["absent"]["label"] == "Absent"


def test_GET_list_returns_localized_label_en(auth_client):
    """English Accept-Language returns English labels."""
    response = auth_client.get(URL, HTTP_ACCEPT_LANGUAGE="en")
    assert response.status_code == 200
    by_code = {row["code"]: row for row in response.json()["results"]}
    assert by_code["present"]["label"] == "Present"
    assert by_code["absent"]["label"] == "Absent"


def test_POST_as_non_staff_returns_403(auth_client):
    response = auth_client.post(
        URL,
        {"code": "tardy", "label_fr": "En retard", "order": 4},
        format="json",
    )
    assert response.status_code == 403


def test_POST_as_staff_returns_201(staff_client):
    response = staff_client.post(
        URL,
        {
            "code": "tardy",
            "label_fr": "En retard",
            "label_nl": "Te laat",
            "label_en": "Tardy",
            "label_it": "In ritardo",
            "label_es": "Tarde",
            "is_default": False,
            "order": 4,
            "color": "#a855f7",
            "is_active": True,
        },
        format="json",
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["code"] == "tardy"
    assert body["label_fr"] == "En retard"
    assert AttendanceStatus.objects.filter(code="tardy").exists()


def test_PATCH_as_staff_modifies_label_fr(staff_client):
    s = AttendanceStatus.objects.get(code="present")
    response = staff_client.patch(
        f"{URL}{s.pk}/",
        {"label_fr": "Ici!"},
        format="json",
    )
    assert response.status_code == 200, response.json()
    s.refresh_from_db()
    assert s.label_fr == "Ici!"


def test_DELETE_soft_deletes(staff_client):
    new = AttendanceStatus.objects.create(code="z_test", label="Z", is_default=False, order=99)
    response = staff_client.delete(f"{URL}{new.pk}/")
    assert response.status_code == 204
    new.refresh_from_db()
    assert new.is_active is False


def test_GET_include_inactive_as_staff_includes_inactive(staff_client):
    inactive = AttendanceStatus.objects.create(
        code="inact_test", label="Inact", is_default=False, order=98, is_active=False
    )
    response = staff_client.get(f"{URL}?include_inactive=true")
    assert response.status_code == 200
    codes = {row["code"] for row in response.json()["results"]}
    assert "inact_test" in codes
    inactive.delete()


def test_GET_include_inactive_as_non_staff_ignored(auth_client):
    inactive = AttendanceStatus.objects.create(
        code="inact_ns", label="X", is_default=False, order=97, is_active=False
    )
    response = auth_client.get(f"{URL}?include_inactive=true")
    assert response.status_code == 200
    codes = {row["code"] for row in response.json()["results"]}
    assert "inact_ns" not in codes
    inactive.delete()

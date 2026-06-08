"""Rich-text (Quill HTML) goal + equipment on Event.

Coverage:
  - PATCH goal/equipment with a <script> payload -> stored sanitized
    (script stripped, safe tags kept).
  - strip_html returns plain text (tags removed, entities unescaped).
"""

import pytest

from tests.factories import EventFactory, ProgramFactory
from tools.html_sanitizer import strip_html

pytestmark = pytest.mark.django_db


@pytest.fixture
def trainer_event(trainer_user):
    team = trainer_user.owned_teams.first()
    program = ProgramFactory(team=team)
    return EventFactory(refer_program=program)


def test_patch_goal_sanitizes_html(auth_client_trainer, trainer_event):
    resp = auth_client_trainer.patch(
        f"/api/v1/events/{trainer_event.pk}/",
        {"goal": "<script>alert(1)</script><b>x</b>"},
        format="json",
    )
    assert resp.status_code == 200, resp.content

    trainer_event.refresh_from_db()
    stored = trainer_event.goal
    # The <script> element is stripped (no executable markup) while safe
    # tags are preserved. bleach keeps the (now-inert) text content.
    assert "<script" not in stored.lower()
    assert "</script>" not in stored.lower()
    assert "<b>x</b>" in stored


def test_patch_equipment_sanitizes_html(auth_client_trainer, trainer_event):
    resp = auth_client_trainer.patch(
        f"/api/v1/events/{trainer_event.pk}/",
        {"equipment": "<script>alert(1)</script><b>x</b>"},
        format="json",
    )
    assert resp.status_code == 200, resp.content

    trainer_event.refresh_from_db()
    stored = trainer_event.equipment
    assert "<script" not in stored.lower()
    assert "</script>" not in stored.lower()
    assert "<b>x</b>" in stored


def test_detail_api_serves_sanitized_html_as_is(auth_client_trainer, trainer_event):
    """The authenticated detail API keeps serving the sanitized HTML (the
    frontend renders it via innerHTML) — it must NOT be tag-stripped."""
    trainer_event.goal = "<b>keep me</b>"
    trainer_event.save(update_fields=["goal"])

    resp = auth_client_trainer.get(f"/api/v1/events/{trainer_event.pk}/")
    assert resp.status_code == 200
    assert resp.json()["goal"] == "<b>keep me</b>"


def test_strip_html_returns_plain_text():
    assert strip_html("<b>Hello</b> <i>world</i>") == "Hello world"
    # tags are removed; no markup leaks
    out = strip_html("<p>line</p>")
    assert "<" not in out and ">" not in out
    # entities are unescaped to readable text
    assert strip_html("a &amp; b") == "a & b"
    assert strip_html("") == ""
    assert strip_html(None) == ""


def test_empty_goal_stays_empty(auth_client_trainer, trainer_event):
    resp = auth_client_trainer.patch(
        f"/api/v1/events/{trainer_event.pk}/",
        {"goal": ""},
        format="json",
    )
    assert resp.status_code == 200
    trainer_event.refresh_from_db()
    assert trainer_event.goal in ("", None)

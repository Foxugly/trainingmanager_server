# Event Training Types Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an Event's training content polymorphic — `STRUCTURED` (existing rounds+exercises, for swimming) or `FREEFORM` (a sanitized rich-text blob, for other sports), mutually exclusive, with a sport→team-sport→event type cascade and type-aware AI generation.

**Architecture:** Approach 1 from `docs/event-training-types-design.md` — a `training_type` discriminator + co-existing nullable content on `Event` (keep `rounds` M2M; add `training_richtext` TextField). Type is seeded at event creation from `TeamSport.training_type ?? Sport.default_training_type` and then owned/editable on the event. Switching type clears the now-inactive content.

**Tech Stack:** Django 6 + DRF + drf-spectacular (backend, repo `D:\PycharmProjects\trainingmanager_server`, venv `.venv\Scripts\python.exe`, tests `pytest`); Angular 21 standalone/signals + PrimeNG + Transloco (frontend, repo `C:\Users\Renaud\WebstormProjects\trainingmanager_frontend`, tests `npx ng test --watch=false`); Anthropic tool-use for AI.

**Conventions:** TDD; commit after each green task; translatable strings via `gettext_lazy as _`; new i18n keys go into all 5 frontend locales `public/i18n/{fr,nl,en,it,es}.json`; after backend schema changes, re-sync `frontend/openapi/Training_Manager_API.yaml` from backend `openapi-schema.yaml` and `npm run api:gen`. Backend deploy = push `main` (OIDC→SSM auto-deploy); do not push until the whole feature is green.

---

## File Structure

**Backend (`trainingmanager_server`)**
- Create `tools/choices.py` — `TrainingType` TextChoices (neutral util module, no app imports → no circular-import risk; `sport`/`team`/`event` models import it).
- Modify `sport/models.py` — add `Sport.default_training_type`.
- Modify `team/models.py` — add `TeamSport.training_type` (nullable).
- Modify `event/models.py` — add `Event.training_type` + `Event.training_richtext`; add `Event.resolve_default_training_type()`.
- Migrations: `sport/migrations/00XX_*`, `team/migrations/00XX_*`, `event/migrations/00XX_*` (fields carry `default=STRUCTURED`/`null=True`, so existing rows backfill automatically — no data migration needed).
- Modify `django-trainingmanager/settings/base.py` — register `TrainingTypeEnum` in `ENUM_NAME_OVERRIDES`.
- Modify `sport/serializers.py` — `default_training_type` on `SportSerializer` (read) + `SportAdminSerializer` (write).
- Modify `team/serializers.py` — `training_type` on `TeamSportReadSerializer` (read) + per-sport write path.
- Modify `event/serializers.py` — `training_type` (write) + `training_richtext` (write, sanitized) + create-seeding + switch-clear in update + freeform visibility redaction.
- Modify `event/ai.py` — add `generate_freeform_training(...)` + freeform tool schema/prompt.
- Modify `event/views.py` — `generate_training` branches on `event.training_type`.
- Tests in `tests/`: `test_training_types.py` (new), extend `test_event_*`, `test_team_*`.

**Frontend (`trainingmanager_frontend`)**
- Regenerated `src/app/api/**` (enum `TrainingTypeEnum`, new fields).
- Modify `src/app/features/events/events-detail/events-detail.component.{ts,html}` — branch training area on type.
- Create `src/app/features/events/event-freeform/event-freeform.component.{ts,html,spec.ts}` — freeform editor panel.
- Modify `src/app/features/events/event-training/event-training.component.ts` — (no behavioural change; still the STRUCTURED panel).
- Modify `src/app/features/events/events-form` or events-detail — training_type selector + confirm-on-switch.
- Modify `src/app/features/teams/teams-form/teams-form.component.{ts,html}` — per-sport training_type override.
- Modify `src/app/features/admin/sports/sports-form/sports-form.component.{ts,html}` — default_training_type dropdown.
- `public/i18n/{fr,nl,en,it,es}.json` — new keys.

---

## Phase 1 — Backend: enum + model fields + migrations

### Task 1: `TrainingType` enum

**Files:**
- Create: `tools/choices.py`
- Modify: `django-trainingmanager/settings/base.py` (ENUM_NAME_OVERRIDES)
- Test: `tests/test_training_types.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_training_types.py
import pytest
from tools.choices import TrainingType


def test_training_type_values():
    assert TrainingType.STRUCTURED == "structured"
    assert TrainingType.FREEFORM == "freeform"
    # Exactly the two shipped values (future ones added deliberately).
    assert set(TrainingType.values) == {"structured", "freeform"}
```

- [ ] **Step 2: Run it — expect ImportError**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_types.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.choices'`.

- [ ] **Step 3: Create the enum**

```python
# tools/choices.py
from django.db import models
from django.utils.translation import gettext_lazy as _


class TrainingType(models.TextChoices):
    """The kind of training content an Event carries (mutually exclusive).

    STRUCTURED — rounds + exercises (the swimming model).
    FREEFORM   — a single sanitized rich-text HTML blob.
    Add a new value here + a content field on Event + a branch to extend.
    """

    STRUCTURED = "structured", _("Structured (rounds & exercises)")
    FREEFORM = "freeform", _("Free text")
```

- [ ] **Step 4: Register the enum name for the schema**

In `django-trainingmanager/settings/base.py`, inside `SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"]`, add:

```python
        "TrainingTypeEnum": "tools.choices.TrainingType",
```

- [ ] **Step 5: Run — expect PASS**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_types.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tools/choices.py django-trainingmanager/settings/base.py tests/test_training_types.py
git commit -m "feat(training): add TrainingType enum + schema name override"
```

### Task 2: `Sport.default_training_type`

**Files:**
- Modify: `sport/models.py`
- Create: `sport/migrations/00XX_sport_default_training_type.py` (via makemigrations)
- Test: `tests/test_training_types.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_training_types.py
from tests.factories import SportFactory

pytestmark = pytest.mark.django_db  # add at top of file if not present


def test_sport_default_training_type_defaults_structured():
    sport = SportFactory()
    assert sport.default_training_type == TrainingType.STRUCTURED
```

- [ ] **Step 2: Run — expect AttributeError**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_types.py::test_sport_default_training_type_defaults_structured -q`
Expected: FAIL — `AttributeError: 'Sport' object has no attribute 'default_training_type'`.

- [ ] **Step 3: Add the field**

In `sport/models.py`, add the import and field to `class Sport`:

```python
from tools.choices import TrainingType  # at top

class Sport(models.Model):
    # ... existing fields ...
    default_training_type = models.CharField(
        max_length=20,
        choices=TrainingType.choices,
        default=TrainingType.STRUCTURED,
        help_text=_(
            "Default training-content type for events of this sport "
            "(overridable per team and per event)."
        ),
    )
```

- [ ] **Step 4: Make + apply the migration**

Run: `.venv\Scripts\python.exe manage.py makemigrations sport`
Expected: a new migration adding `default_training_type` (existing rows backfill to `structured` via the field default).
Run: `.venv\Scripts\python.exe -m pytest tests/test_training_types.py::test_sport_default_training_type_defaults_structured -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sport/models.py sport/migrations/ tests/test_training_types.py
git commit -m "feat(training): Sport.default_training_type (default structured)"
```

### Task 3: `TeamSport.training_type` (nullable override)

**Files:**
- Modify: `team/models.py`
- Create: `team/migrations/00XX_teamsport_training_type.py`
- Test: `tests/test_training_types.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_training_types.py
from team.models import TeamSport


def test_teamsport_training_type_nullable_default_none():
    sport = SportFactory()
    from tests.factories import TeamFactory
    team = TeamFactory()  # creates its default TeamSport
    ts = TeamSport.objects.filter(team=team).first()
    assert ts.training_type is None  # null = inherit the sport default
```

- [ ] **Step 2: Run — expect AttributeError/FieldError**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_types.py::test_teamsport_training_type_nullable_default_none -q`
Expected: FAIL — no such field.

- [ ] **Step 3: Add the field**

In `team/models.py`, add the import and field to `class TeamSport`:

```python
from tools.choices import TrainingType  # at top

class TeamSport(models.Model):
    # ... existing fields ...
    training_type = models.CharField(
        max_length=20,
        choices=TrainingType.choices,
        null=True,
        blank=True,
        default=None,
        help_text=_(
            "Team override of the sport's default training type. "
            "Null = inherit Sport.default_training_type."
        ),
    )
```

- [ ] **Step 4: Make migration + run**

Run: `.venv\Scripts\python.exe manage.py makemigrations team`
Run: `.venv\Scripts\python.exe -m pytest tests/test_training_types.py::test_teamsport_training_type_nullable_default_none -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add team/models.py team/migrations/ tests/test_training_types.py
git commit -m "feat(training): TeamSport.training_type nullable override"
```

### Task 4: `Event.training_type` + `Event.training_richtext`

**Files:**
- Modify: `event/models.py`
- Create: `event/migrations/00XX_event_training_type_richtext.py`
- Test: `tests/test_training_types.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_training_types.py
from tests.factories import EventFactory
from event.models import Event


def test_event_training_fields_defaults():
    event = EventFactory()
    assert event.training_type == TrainingType.STRUCTURED
    assert event.training_richtext == ""
```

- [ ] **Step 2: Run — expect failure (no such fields)**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_types.py::test_event_training_fields_defaults -q`
Expected: FAIL.

- [ ] **Step 3: Add the fields**

In `event/models.py`, add the import and fields to `class Event`:

```python
from tools.choices import TrainingType  # at top

class Event(models.Model):
    # ... existing fields ...
    training_type = models.CharField(
        max_length=20,
        choices=TrainingType.choices,
        default=TrainingType.STRUCTURED,
        help_text=_(
            "This event's active training-content type. Seeded at creation "
            "from the team-sport / sport cascade; editable by the coach."
        ),
    )
    training_richtext = models.TextField(
        blank=True,
        default="",
        help_text=_("Free-text training content (sanitized HTML) when training_type=freeform."),
    )
```

- [ ] **Step 4: Make migration + run**

Run: `.venv\Scripts\python.exe manage.py makemigrations event`
Expected: migration adds both fields; existing events backfill to `structured` / `""`.
Run: `.venv\Scripts\python.exe -m pytest tests/test_training_types.py::test_event_training_fields_defaults -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add event/models.py event/migrations/ tests/test_training_types.py
git commit -m "feat(training): Event.training_type + training_richtext fields"
```

---

## Phase 2 — Backend: cascade resolution + serializers

### Task 5: `Event.resolve_default_training_type()` cascade helper

**Files:**
- Modify: `event/models.py`
- Test: `tests/test_training_types.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_training_types.py
from program.models import Program


def _event_for(team, sport):
    program = Program.objects.create(name="P", team=team)
    return EventFactory(refer_program=program, sport=sport)


def test_cascade_sport_default_only():
    sport = SportFactory()  # default_training_type=structured
    from tests.factories import TeamFactory
    team = TeamFactory(sport=sport)
    event = _event_for(team, sport)
    assert event.resolve_default_training_type() == TrainingType.STRUCTURED


def test_cascade_team_override_beats_sport_default():
    sport = SportFactory()
    from tests.factories import TeamFactory
    team = TeamFactory(sport=sport)
    ts = TeamSport.objects.get(team=team, sport=sport)
    ts.training_type = TrainingType.FREEFORM
    ts.save()
    event = _event_for(team, sport)
    assert event.resolve_default_training_type() == TrainingType.FREEFORM


def test_cascade_falls_back_when_no_sport():
    from tests.factories import TeamFactory
    team = TeamFactory()
    program = Program.objects.create(name="P", team=team)
    event = EventFactory(refer_program=program, sport=None)
    assert event.resolve_default_training_type() == TrainingType.STRUCTURED
```

- [ ] **Step 2: Run — expect AttributeError**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_types.py -k cascade -q`
Expected: FAIL — no `resolve_default_training_type`.

- [ ] **Step 3: Implement the helper**

In `event/models.py` on `class Event` (uses `self.team` which already resolves the event's team via `refer_program`):

```python
    def resolve_default_training_type(self):
        """Cascade for the *initial* training type of this event:
        TeamSport override (team+sport) ?? Sport default ?? STRUCTURED.
        Only used to seed `training_type` at creation; the event owns its
        type thereafter."""
        sport = self.sport
        team = self.team
        if sport is not None and team is not None:
            from team.models import TeamSport

            ts = TeamSport.objects.filter(team=team, sport=sport).first()
            if ts is not None and ts.training_type:
                return ts.training_type
        if sport is not None and sport.default_training_type:
            return sport.default_training_type
        return TrainingType.STRUCTURED
```

> Note: confirm `Event.team` exists (it is used by `EventSerializer._requester_is_manager`). It resolves `refer_program.team`. If it is a property returning None for orphan events, the guards above handle it.

- [ ] **Step 4: Run — expect PASS**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_types.py -k cascade -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add event/models.py tests/test_training_types.py
git commit -m "feat(training): Event.resolve_default_training_type cascade"
```

### Task 6: EventSerializer — expose fields, seed on create, switch-clear on update

**Files:**
- Modify: `event/serializers.py`
- Test: `tests/test_training_types.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_training_types.py
def test_event_create_seeds_training_type_from_cascade(auth_client_trainer, trainer_user, trainer_sport):
    team = trainer_user.owned_teams.first()
    ts = TeamSport.objects.get(team=team, sport=trainer_sport)
    ts.training_type = TrainingType.FREEFORM
    ts.save()
    program = Program.objects.create(name="P", team=team)
    resp = auth_client_trainer.post(
        "/api/v1/events/",
        {"name": "E", "refer_program_id": program.pk, "sport_id": trainer_sport.pk},
        format="json",
    )
    assert resp.status_code == 201, resp.json()
    assert resp.json()["training_type"] == "freeform"


def test_event_patch_freeform_richtext_is_sanitized(auth_client_trainer, trainer_user, trainer_sport):
    team = trainer_user.owned_teams.first()
    program = Program.objects.create(name="P", team=team)
    event = EventFactory(refer_program=program, sport=trainer_sport, training_type=TrainingType.FREEFORM)
    resp = auth_client_trainer.patch(
        f"/api/v1/events/{event.pk}/",
        {"training_richtext": "<p>Swim</p><script>alert(1)</script>"},
        format="json",
    )
    assert resp.status_code == 200, resp.json()
    event.refresh_from_db()
    assert "<script>" not in event.training_richtext
    assert "<p>Swim</p>" in event.training_richtext


def test_switch_to_freeform_clears_rounds(auth_client_trainer, trainer_user, trainer_sport):
    from round.models import Round
    team = trainer_user.owned_teams.first()
    program = Program.objects.create(name="P", team=team)
    event = EventFactory(refer_program=program, sport=trainer_sport, training_type=TrainingType.STRUCTURED)
    r = Round.objects.create(sport=trainer_sport, language="fr", order=1, count=1)
    event.rounds.add(r)
    resp = auth_client_trainer.patch(
        f"/api/v1/events/{event.pk}/", {"training_type": "freeform"}, format="json"
    )
    assert resp.status_code == 200, resp.json()
    event.refresh_from_db()
    assert event.rounds.count() == 0


def test_switch_to_structured_clears_richtext(auth_client_trainer, trainer_user, trainer_sport):
    team = trainer_user.owned_teams.first()
    program = Program.objects.create(name="P", team=team)
    event = EventFactory(
        refer_program=program, sport=trainer_sport,
        training_type=TrainingType.FREEFORM, training_richtext="<p>x</p>",
    )
    resp = auth_client_trainer.patch(
        f"/api/v1/events/{event.pk}/", {"training_type": "structured"}, format="json"
    )
    assert resp.status_code == 200, resp.json()
    event.refresh_from_db()
    assert event.training_richtext == ""
```

> `trainer_sport` / `trainer_user` / `auth_client_trainer` are existing conftest fixtures (see `tests/test_round_attach.py`). Confirm `trainer_sport` is the sport of `trainer_user`'s team; if the fixture name differs, mirror what `test_round_attach.py` uses.

- [ ] **Step 2: Run — expect failures (fields absent / no seeding / no clearing)**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_types.py -k "seeds or sanitized or clears" -q`
Expected: FAIL.

- [ ] **Step 3: Edit `EventSerializer`**

(a) Add to `Meta.fields` (after `"rounds_detail"`): `"training_type", "training_richtext"`. Do NOT add them to `read_only_fields` (both writable).

(b) Add a sanitizer validator (mirrors `validate_goal`):

```python
    def validate_training_richtext(self, value):
        """Sanitize the free-text (Quill) HTML training content on write."""
        if not value:
            return value
        return sanitize_html(value)
```

(c) Seed on create — extend the existing `create()` (after the sport-defaulting block, before `super().create`):

```python
    def create(self, validated_data):
        validated_data = self._sync_location(validated_data)
        validated_data = self._sync_equipment(validated_data)
        if validated_data.get("sport") is None:
            program = validated_data.get("refer_program")
            team = program.team if program is not None else None
            if team is not None:
                validated_data["sport"] = team.default_sport
        # Seed the training type from the cascade unless explicitly provided.
        if "training_type" not in validated_data:
            seed = Event(
                refer_program=validated_data.get("refer_program"),
                sport=validated_data.get("sport"),
            ).resolve_default_training_type()
            validated_data["training_type"] = seed
        return super().create(validated_data)
```

(d) Switch-clear on update — extend the existing `update()`:

```python
    def update(self, instance, validated_data):
        validated_data = self._sync_location(validated_data)
        validated_data = self._sync_equipment(validated_data)
        new_type = validated_data.get("training_type", instance.training_type)
        type_changed = new_type != instance.training_type
        instance = super().update(instance, validated_data)
        if type_changed:
            from tools.choices import TrainingType

            if new_type != TrainingType.STRUCTURED:
                instance.rounds.clear()
            if new_type != TrainingType.FREEFORM and instance.training_richtext:
                instance.training_richtext = ""
                instance.save(update_fields=["training_richtext"])
        return instance
```

- [ ] **Step 4: Run — expect PASS**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_types.py -k "seeds or sanitized or clears" -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add event/serializers.py tests/test_training_types.py
git commit -m "feat(training): EventSerializer training_type/richtext + seed + switch-clear"
```

### Task 7: Sport serializers expose `default_training_type`

**Files:**
- Modify: `sport/serializers.py`
- Test: `tests/test_training_types.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_training_types.py
def test_sport_admin_can_set_default_training_type(api_client):
    from tests.factories import UserFactory
    staff = UserFactory(is_staff=True)
    api_client.force_authenticate(user=staff)
    sport = SportFactory()
    resp = api_client.patch(
        f"/api/v1/sports/{sport.pk}/", {"default_training_type": "freeform"}, format="json"
    )
    assert resp.status_code == 200, resp.json()
    sport.refresh_from_db()
    assert sport.default_training_type == "freeform"
```

> Confirm the admin sport update route/permission (staff-only). If sports admin uses a different path, mirror an existing test in `tests/` (e.g. a sports admin test). Adjust the URL if needed.

- [ ] **Step 2: Run — expect FAIL (field not writable / not present)**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_types.py -k sport_admin -q`
Expected: FAIL.

- [ ] **Step 3: Add the field to both serializers**

In `sport/serializers.py`:
- `SportSerializer.Meta.fields`: add `"default_training_type"` (stays in `read_only_fields = fields`).
- `SportAdminSerializer.Meta.fields`: add `"default_training_type"` (writable — not in `read_only_fields`).

- [ ] **Step 4: Run — expect PASS**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_types.py -k sport_admin -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add sport/serializers.py tests/test_training_types.py
git commit -m "feat(training): expose Sport.default_training_type in serializers"
```

### Task 8: Team serializer — per-sport training_type read + write

**Files:**
- Modify: `team/serializers.py`
- Test: `tests/test_training_types.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_training_types.py
def test_team_sports_read_includes_training_type(auth_client_trainer, trainer_user, trainer_sport):
    team = trainer_user.owned_teams.first()
    ts = TeamSport.objects.get(team=team, sport=trainer_sport)
    ts.training_type = TrainingType.FREEFORM
    ts.save()
    resp = auth_client_trainer.get(f"/api/v1/teams/{team.pk}/")
    assert resp.status_code == 200
    sports = resp.json()["sports"]
    row = next(s for s in sports if s["id"] == trainer_sport.pk)
    assert row["training_type"] == "freeform"


def test_team_can_set_per_sport_training_type(auth_client_trainer, trainer_user, trainer_sport):
    team = trainer_user.owned_teams.first()
    resp = auth_client_trainer.patch(
        f"/api/v1/teams/{team.pk}/",
        {"sport_training_types": [{"sport_id": trainer_sport.pk, "training_type": "freeform"}]},
        format="json",
    )
    assert resp.status_code == 200, resp.json()
    ts = TeamSport.objects.get(team=team, sport=trainer_sport)
    assert ts.training_type == "freeform"
```

- [ ] **Step 2: Run — expect FAIL**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_types.py -k team_ -q`
Expected: FAIL.

- [ ] **Step 3a: Read field on `TeamSportReadSerializer`**

In `team/serializers.py`, add `training_type` to `TeamSportReadSerializer`:

```python
class TeamSportReadSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(source="sport.id", read_only=True)
    name = serializers.CharField(source="sport.name", read_only=True)
    slug = serializers.SlugField(source="sport.slug", read_only=True)

    class Meta:
        model = TeamSport
        fields = ["id", "name", "slug", "is_default", "order", "training_type"]
        read_only_fields = fields
```

- [ ] **Step 3b: Write path on `TeamSerializer`**

Add a write-only field + an inline item serializer, and apply it in create/update.

```python
# near the other write-only declarations on TeamSerializer
class _SportTrainingTypeWriteSerializer(serializers.Serializer):
    sport_id = serializers.PrimaryKeyRelatedField(queryset=Sport.objects.all())
    training_type = serializers.ChoiceField(
        choices=TrainingType.choices, allow_null=True, required=False
    )

class TeamSerializer(serializers.ModelSerializer):
    # ... existing fields ...
    sport_training_types = _SportTrainingTypeWriteSerializer(
        many=True, write_only=True, required=False
    )
```

Add the import `from tools.choices import TrainingType` at the top. Then in `create()` and `update()`, pop and apply it AFTER `self._persist_sports(...)` (so the TeamSport rows already exist):

```python
        overrides = validated_data.pop("sport_training_types", None)
        # ... existing body that ends by calling self._persist_sports(team, ...) ...
        self._apply_sport_training_types(team, overrides)
        return team
```

And add the helper on `TeamSerializer`:

```python
    def _apply_sport_training_types(self, team, overrides):
        """Set TeamSport.training_type for the given (sport_id, training_type)
        pairs. Only touches sports the team actually has; null clears the
        override (inherit the sport default)."""
        if not overrides:
            return
        for item in overrides:
            sport = item["sport_id"]
            ts = team.team_sports.filter(sport=sport).first()
            if ts is not None:
                ts.training_type = item.get("training_type")
                ts.save(update_fields=["training_type"])
```

> `pop("sport_training_types", None)` must run in BOTH `create()` and `update()` before `super().create/update` (it is not a model field). Place the `pop` alongside the existing `sport_ids`/`default_sport_id` pops, and the `_apply_...` call at the end (mirroring where `_persist_sports` is called).

- [ ] **Step 4: Run — expect PASS**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_types.py -k team_ -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add team/serializers.py tests/test_training_types.py
git commit -m "feat(training): team per-sport training_type read + write"
```

---

## Phase 3 — Backend: type-aware AI generation

### Task 9: Freeform AI generation in `event/ai.py`

**Files:**
- Modify: `event/ai.py`
- Test: `tests/test_training_types.py`

- [ ] **Step 1: Write the failing test (mock Claude)**

```python
# append to tests/test_training_types.py
from unittest.mock import patch


def test_generate_freeform_training_returns_html(trainer_user, trainer_sport):
    from event.ai import generate_freeform_training
    from tests.factories import TeamFactory
    team = trainer_user.owned_teams.first()
    program = Program.objects.create(name="P", team=team)
    event = EventFactory(refer_program=program, sport=trainer_sport, training_type=TrainingType.FREEFORM)

    fake = {
        "tool_input": {"html": "<p>Easy run 30'</p>", "rationale": "Recovery day."},
        "model": "claude-x", "input_tokens": 10, "output_tokens": 5,
    }
    with patch("event.ai.call_claude_with_tool", return_value=fake) as mocked:
        out = generate_freeform_training(event=event, user=trainer_user, additional_prompt="easy")
    assert out["html"] == "<p>Easy run 30'</p>"
    assert out["rationale"] == "Recovery day."
    # The team language must be passed into the prompt build (fr by default).
    assert mocked.called
```

- [ ] **Step 2: Run — expect ImportError**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_types.py -k freeform_training -q`
Expected: FAIL — no `generate_freeform_training`.

- [ ] **Step 3: Implement the freeform generator**

In `event/ai.py`, add a tool schema + generator. Reuse `call_claude_with_tool`, `build_system_prompt` patterns already in the file. The target language is the team's `language`.

```python
def build_freeform_tool_schema():
    return {
        "name": "create_freeform_training",
        "description": (
            "Generate a free-text training session as a short HTML document "
            "(headings, paragraphs, lists). No structured rounds."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "html": {
                    "type": "string",
                    "description": (
                        "The session as sanitizable HTML (use only <p>, <ul>/<ol>/<li>, "
                        "<strong>, <em>, <h2>/<h3>, <br>). Max ~4000 chars."
                    ),
                    "maxLength": 8000,
                },
                "rationale": {"type": "string", "description": "1-3 sentence explanation."},
            },
            "required": ["html", "rationale"],
        },
    }


def generate_freeform_training(*, event, user=None, additional_prompt=""):
    """Generate free-text (HTML) training content via Claude, in the team's
    language. Returns {html, rationale, prompt_sent, model, input_tokens,
    output_tokens}."""
    team = event.refer_program.team if event.refer_program else None
    sport = event.sport or (team.default_sport if team else None)
    sport_name = sport.name if sport else "the practiced sport"
    language = team.language if team is not None else "fr"

    tool = build_freeform_tool_schema()
    system = build_system_prompt(sport_name)
    language_names = {"fr": "French", "nl": "Dutch", "en": "English", "it": "Italian", "es": "Spanish"}
    lang_label = language_names.get(language, "French")
    user_prompt = (
        f"Write a {sport_name} training session for the event '{event.name}'"
        f"{(' on ' + str(event.date)) if event.date else ''}. "
        f"Write ALL prose in {lang_label}. "
        f"Return it via the create_freeform_training tool as concise HTML. "
        f"{('Extra guidance: ' + additional_prompt) if additional_prompt else ''}"
    )

    result = call_claude_with_tool(
        prompt=user_prompt,
        system=system,
        cached_prefix="",
        tool=tool,
        track_kwargs={"team": team, "user": user, "endpoint": "training_freeform"},
    )
    tool_input = result["tool_input"]
    html = tool_input.get("html", "")
    if not html:
        raise AIServiceError(_("AI returned empty free-text content."))
    return {
        "html": html,
        "rationale": tool_input.get("rationale", ""),
        "prompt_sent": user_prompt,
        "model": result["model"],
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
    }
```

> Check the actual names of `build_system_prompt` / `call_claude_with_tool` / `AIServiceError` in `event/ai.py` and import/use them exactly. The `track_kwargs["endpoint"]` value `"training_freeform"` is new — confirm the aiusage tracker accepts arbitrary endpoint strings (it does; it just records the string).

- [ ] **Step 4: Run — expect PASS**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_types.py -k freeform_training -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add event/ai.py tests/test_training_types.py
git commit -m "feat(training): AI freeform training generation (team language)"
```

### Task 10: `generate_training` view branches on type

**Files:**
- Modify: `event/views.py`
- Test: `tests/test_training_types.py`

- [ ] **Step 1: Write the failing test (mock both generators)**

```python
# append to tests/test_training_types.py
def test_generate_training_freeform_writes_richtext(auth_client_trainer, trainer_user, trainer_sport):
    team = trainer_user.owned_teams.first()
    program = Program.objects.create(name="P", team=team)
    from datetime import date, timedelta
    event = EventFactory(
        refer_program=program, sport=trainer_sport,
        training_type=TrainingType.FREEFORM, date=date.today() + timedelta(days=3),
    )
    fake = {
        "html": "<p>Recovery</p>", "rationale": "easy",
        "prompt_sent": "p", "model": "m", "input_tokens": 1, "output_tokens": 1,
    }
    with patch("event.views.ai_generate_freeform_training", return_value=fake):
        resp = auth_client_trainer.post(
            f"/api/v1/events/{event.pk}/generate-training/", {}, format="json"
        )
    assert resp.status_code == 200, resp.json()
    event.refresh_from_db()
    assert "<p>Recovery</p>" in event.training_richtext
    assert event.rounds.count() == 0
    assert event.generated_by_ai is True
```

> Match the import alias used in `event/views.py` for the AI functions. The structured one is imported as `ai_generate_training`; import the freeform one as `ai_generate_freeform_training` (`from event.ai import generate_freeform_training as ai_generate_freeform_training`). Patch the name as it exists in `event.views`.

- [ ] **Step 2: Run — expect FAIL**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_types.py -k generate_training_freeform -q`
Expected: FAIL.

- [ ] **Step 3: Branch the view**

In `event/views.py`, import the freeform generator near the existing `ai_generate_training` import:

```python
from event.ai import generate_freeform_training as ai_generate_freeform_training
```

In `generate_training`, after the manager check and the past-date check, replace the single "already has rounds" guard + structured body with a branch on `event.training_type`:

```python
        from tools.choices import TrainingType

        if event.training_type == TrainingType.FREEFORM:
            if event.training_richtext:
                return Response(
                    {"code": "event_has_training", "detail": _("Event already has free-text content. Clear it before regenerating.")},
                    status=status.HTTP_409_CONFLICT,
                )
            body_serializer = GenerateTrainingRequestSerializer(data=request.data)
            body_serializer.is_valid(raise_exception=True)
            additional_prompt = body_serializer.validated_data.get("additional_prompt", "")
            ai_result = ai_generate_freeform_training(
                event=event,
                user=request.user if request.user.is_authenticated else None,
                additional_prompt=additional_prompt,
            )
            from tools.html_sanitizer import sanitize_html

            event.training_richtext = sanitize_html(ai_result["html"])
            event.generated_by_ai = True
            event.ai_prompt = ai_result["prompt_sent"]
            event.ai_response = ai_result["rationale"]
            event.ai_generated_at = timezone.now()
            event.save()
            return Response(
                {
                    "rationale": ai_result["rationale"],
                    "model": ai_result["model"],
                    "tokens_used": {"input": ai_result["input_tokens"], "output": ai_result["output_tokens"]},
                },
                status=status.HTTP_200_OK,
            )

        # --- STRUCTURED path (existing code below, unchanged) ---
        if event.rounds.exists():
            return Response(... )  # existing 409 guard
        # ... existing structured generation ...
```

> Keep the existing structured branch exactly as-is below the freeform branch. The `GenerateTrainingResponse` schema (frontend) already tolerates absent `rounds_created` fields; if drf-spectacular complains, widen the `@extend_schema` 200 response doc to note both shapes (optional — describe in the docstring).

- [ ] **Step 4: Run — expect PASS + existing structured AI tests still green**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_types.py -k generate_training_freeform tests/test_event*.py -q`
Expected: PASS; existing structured generate tests unaffected.

- [ ] **Step 5: Commit**

```bash
git add event/views.py tests/test_training_types.py
git commit -m "feat(training): generate-training branches structured vs freeform"
```

---

## Phase 4 — Backend: visibility redaction + schema

### Task 11: Hide `training_richtext` under the `vis_rounds` gate for non-managers

**Files:**
- Modify: `event/serializers.py`
- Test: `tests/test_training_types.py`

- [ ] **Step 1: Read the current redaction**

Open `event/serializers.py` and locate where `vis_rounds` is applied for non-managers (the `to_representation` block that blanks `rounds`/`rounds_detail` — it uses `aspect_visible_to_athlete("rounds")` / `_requester_is_manager`). The freeform content must be blanked by the SAME rule.

- [ ] **Step 2: Write the failing test**

```python
# append to tests/test_training_types.py
from event.models import VisibilityMode


def test_freeform_richtext_redacted_for_non_manager_when_vis_never(api_client, trainer_user, trainer_sport):
    from tests.factories import UserFactory, MemberFactory
    team = trainer_user.owned_teams.first()
    program = Program.objects.create(name="P", team=team)
    event = EventFactory(
        refer_program=program, sport=trainer_sport,
        training_type=TrainingType.FREEFORM, training_richtext="<p>secret</p>",
        vis_rounds=VisibilityMode.NEVER,
    )
    athlete = UserFactory()
    member = MemberFactory(user=athlete)
    from team.models import TeamMembership
    TeamMembership.objects.create(team=team, member=member)
    api_client.force_authenticate(user=athlete)
    resp = api_client.get(f"/api/v1/events/{event.pk}/")
    assert resp.status_code == 200
    assert resp.json().get("training_richtext") in ("", None)
```

> Adjust membership creation to however other visibility tests attach an athlete (see `tests/test_event_visibility.py`). Reuse its helper if present.

- [ ] **Step 3: Run — expect FAIL (richtext leaks)**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_types.py -k redacted_for_non_manager -q`
Expected: FAIL — `training_richtext` returned in full.

- [ ] **Step 4: Extend the redaction**

In the `to_representation` block that already blanks rounds for a non-manager when the rounds aspect is not visible, add (right where `data["rounds"]`/`rounds_detail` are cleared):

```python
            data["training_richtext"] = ""
```

So the active-content blanking covers both `STRUCTURED` (rounds) and `FREEFORM` (richtext) under the one `vis_rounds` gate.

- [ ] **Step 5: Run — expect PASS + existing visibility tests green**

Run: `.venv\Scripts\python.exe -m pytest tests/test_training_types.py -k redacted_for_non_manager tests/test_event_visibility.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add event/serializers.py tests/test_training_types.py
git commit -m "feat(training): redact freeform richtext under vis_rounds gate"
```

### Task 12: Regenerate the OpenAPI schema + full backend suite

**Files:**
- Modify: `openapi-schema.yaml`

- [ ] **Step 1: Regenerate the schema**

Run: `.venv\Scripts\python.exe manage.py spectacular --file openapi-schema.yaml`
Expected: 1 pre-existing error (DiscussionsUnreadView) only; `TrainingTypeEnum` now present; Event/Sport/TeamSport carry the new fields.

- [ ] **Step 2: Run the full backend suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all green (851+ passed).

- [ ] **Step 3: Commit**

```bash
git add openapi-schema.yaml
git commit -m "chore(training): regenerate OpenAPI schema for training types"
```

---

## Phase 5 — Frontend: client regen + i18n

### Task 13: Re-sync contract, regen client, add i18n keys

**Files:**
- Modify: `frontend/openapi/Training_Manager_API.yaml`, `frontend/src/app/api/**`
- Modify: `frontend/public/i18n/{fr,nl,en,it,es}.json`

- [ ] **Step 1: Sync + regenerate**

```bash
cp D:/PycharmProjects/trainingmanager_server/openapi-schema.yaml \
   C:/Users/Renaud/WebstormProjects/trainingmanager_frontend/openapi/Training_Manager_API.yaml
cd C:/Users/Renaud/WebstormProjects/trainingmanager_frontend
npm run api:gen
```

- [ ] **Step 2: Typecheck (expect 0 app errors; additive change)**

Run: `npx tsc --noEmit -p tsconfig.app.json` → expect 0 errors.
Run: `npx tsc --noEmit -p tsconfig.spec.json` → expect 0 errors.
Verify `grep -c is_staff src/app/api/model/me.ts` == 1.

- [ ] **Step 3: Add i18n keys to ALL 5 locales**

Add (with translated values per locale) under suitable namespaces, e.g.:
```json
"events.training.type_structured": "Structured (rounds & exercises)" / "Structuré (rounds & exercices)" / ...,
"events.training.type_freeform": "Free text" / "Texte libre" / ...,
"events.training.switch_confirm": "Changing the training type will delete the current content. Continue?" / "Changer le type d'entraînement supprimera le contenu actuel. Continuer ?" / ...,
"events.training.freeform_heading": "Training (free text)" / "Entraînement (texte libre)" / ...,
"teams.form.training_type_label": "Training type" / "Type d'entraînement" / ...,
"teams.form.training_type_inherit": "Inherit (sport default)" / "Hériter (défaut du sport)" / ...,
"admin.sports.default_training_type": "Default training type" / "Type d'entraînement par défaut" / ...
```
Keep all 5 catalogs key-aligned (the `i18n-parity.spec.ts` guard must stay green).

- [ ] **Step 4: Commit**

```bash
git add openapi/Training_Manager_API.yaml src/app/api public/i18n
git commit -m "chore(training): regen client + i18n keys for training types"
```

---

## Phase 6 — Frontend: event training branch + freeform editor

### Task 14: `app-event-freeform` panel + branch in events-detail

**Files:**
- Create: `frontend/src/app/features/events/event-freeform/event-freeform.component.{ts,html,spec.ts}`
- Modify: `frontend/src/app/features/events/events-detail/events-detail.component.{ts,html}`

- [ ] **Step 1: Write the failing spec for the freeform panel**

```typescript
// event-freeform.component.spec.ts
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { of } from 'rxjs';
import { EventFreeformComponent } from './event-freeform.component';
import { EventsService } from '../../../api/api/events.service';

describe('EventFreeformComponent', () => {
  let fixture: ComponentFixture<EventFreeformComponent>;
  let eventsMock: { eventsPartialUpdate: ReturnType<typeof vi.fn> };

  beforeEach(async () => {
    eventsMock = { eventsPartialUpdate: vi.fn().mockReturnValue(of({ id: 7, training_richtext: '<p>x</p>' })) };
    await TestBed.configureTestingModule({ imports: [EventFreeformComponent] })
      .overrideComponent(EventFreeformComponent, { set: { template: '', imports: [] } })
      .compileComponents();
    TestBed.overrideProvider(EventsService, { useValue: eventsMock });
    fixture = TestBed.createComponent(EventFreeformComponent);
  });

  it('saves the richtext via eventsPartialUpdate({id, patchedEvent})', () => {
    const c = fixture.componentInstance as unknown as {
      event: { set(v: unknown): void }; draft: { set(v: string): void }; save(): void;
    };
    fixture.componentRef.setInput('event', { id: 7, training_richtext: '' });
    fixture.componentRef.setInput('canManage', true);
    c.draft.set('<p>new</p>');
    c.save();
    expect(eventsMock.eventsPartialUpdate).toHaveBeenCalledWith({
      id: 7, patchedEvent: { training_richtext: '<p>new</p>' },
    });
  });
});
```

- [ ] **Step 2: Run — expect FAIL (component missing)**

Run: `npx ng test --watch=false` (or scope to the file) → FAIL.

- [ ] **Step 3: Implement `app-event-freeform`**

```typescript
// event-freeform.component.ts
import { ChangeDetectionStrategy, Component, computed, inject, input, output, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { DestroyRef } from '@angular/core';
import { TranslocoPipe } from '@jsverse/transloco';
import { Button } from 'primeng/button';
import { MessageService } from 'primeng/api';
import { EventsService } from '../../../api/api/events.service';
import { Event } from '../../../api/model/event';
import { RichEditorComponent } from '../../../shared/ui/rich-editor/rich-editor.component';

@Component({
  selector: 'app-event-freeform',
  imports: [TranslocoPipe, Button, RichEditorComponent],
  templateUrl: './event-freeform.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class EventFreeformComponent {
  private readonly eventsService = inject(EventsService);
  private readonly messageService = inject(MessageService);
  private readonly destroyRef = inject(DestroyRef);

  readonly event = input.required<Event>();
  readonly canManage = input(false);
  readonly reloadRequested = output<void>();

  protected readonly draft = signal<string>('');
  protected readonly saving = signal(false);
  protected readonly content = computed(() => this.event().training_richtext ?? '');

  protected save(): void {
    this.saving.set(true);
    this.eventsService
      .eventsPartialUpdate({ id: this.event().id, patchedEvent: { training_richtext: this.draft() } })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => { this.saving.set(false); this.reloadRequested.emit(); },
        error: () => { this.saving.set(false); this.messageService.add({ severity: 'error', detail: 'common.load_failed' }); },
      });
  }
}
```

```html
<!-- event-freeform.component.html -->
@if (canManage()) {
  <app-rich-editor [style]="{ height: '12rem' }" (ngModelChange)="draft.set($event)" [ngModel]="content()" />
  <p-button [label]="'common.save' | transloco" [loading]="saving()" (onClick)="save()" />
} @else {
  <div class="prose" [innerHTML]="content()"></div>
}
```

> `RichEditorComponent` is a ControlValueAccessor; for a non-form usage bind `[ngModel]` + `(ngModelChange)` and import `FormsModule`. If simpler, wrap in a `formControl`. Match the existing `app-rich-editor` usage. Sanitization is enforced server-side on PATCH; the read-only branch uses `[innerHTML]` of already-sanitized content.

- [ ] **Step 4: Branch in `events-detail`**

In `events-detail.component.html`, replace the unconditional `<app-event-training .../>` block with:

```html
@if (e.training_type === 'structured') {
  <app-event-training [event]="e" [team]="team()" [canManage]="canManage()"
    [restrictedViewer]="isRestrictedViewer()"
    (stateChange)="onTrainingState($event)" (reloadRequested)="reloadEvent()" />
} @else if (e.training_type === 'freeform') {
  <app-event-freeform [event]="e" [canManage]="canManage()" (reloadRequested)="reloadEvent()" />
}
```

Add `EventFreeformComponent` to the `events-detail.component.ts` imports. (Use the `TrainingTypeEnum` import for the comparison if you prefer typed constants over string literals: `e.training_type === TrainingTypeEnum.Structured`.)

- [ ] **Step 5: Run — expect PASS**

Run: `npx ng test --watch=false` (freeform spec green; events-detail spec still green).

- [ ] **Step 6: Commit**

```bash
git add src/app/features/events/event-freeform src/app/features/events/events-detail
git commit -m "feat(training): freeform editor panel + type branch in events-detail"
```

### Task 15: training_type selector + confirm-on-switch

**Files:**
- Modify: `frontend/src/app/features/events/events-form/events-form.component.{ts,html}` (or events-detail header — wherever the manager edits the event)

- [ ] **Step 1: Write the failing spec**

```typescript
// in events-form.component.spec.ts — assert switching type with existing content prompts confirm
it('confirms before changing training_type when content exists', () => {
  const confirmSpy = vi.spyOn(confirmationServiceMock, 'confirm');
  access(component).requestTrainingTypeChange('freeform'); // structured event with rounds
  expect(confirmSpy).toHaveBeenCalled();
});
```

> Mirror how this form already injects `ConfirmationService` (component-provided). If the event form is create-only and type-switching lives in events-detail, put the selector there instead and adapt the spec to that component.

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement the selector**

Add a `p-select` bound to the event's `training_type` with options `structured`/`freeform` (labels via the i18n keys from Task 13). On change, if the current content is non-empty (rounds present or `training_richtext`), open a `ConfirmationService.confirm` using `'events.training.switch_confirm'`; on accept, PATCH `eventsPartialUpdate({ id, patchedEvent: { training_type } })` and `reloadRequested.emit()`; on reject, revert the select. When content is empty, PATCH directly.

```typescript
protected requestTrainingTypeChange(next: string): void {
  const hasContent = this.hasTrainingContent(); // rounds.length || !!training_richtext
  const apply = () => this.patchTrainingType(next);
  if (hasContent) {
    this.confirmationService.confirm({
      message: this.transloco.translate('events.training.switch_confirm'),
      accept: apply,
      reject: () => this.revertSelect(),
    });
  } else {
    apply();
  }
}
```

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add src/app/features/events
git commit -m "feat(training): event training_type selector with confirm-on-switch"
```

---

## Phase 7 — Frontend: team + sport config

### Task 16: Per-sport training_type override in teams-form

**Files:**
- Modify: `frontend/src/app/features/teams/teams-form/teams-form.component.{ts,html}`
- Test: `teams-form.component.spec.ts`

- [ ] **Step 1: Write the failing spec**

```typescript
it('submits sport_training_types overrides', () => {
  // select a sport, set its training type to freeform, submit
  access(component).setSportTrainingType(SPORT_ID, 'freeform');
  access(component).submit();
  expect(teamsMock.teamsPartialUpdate).toHaveBeenCalledWith(
    expect.objectContaining({
      patchedTeam: expect.objectContaining({
        sport_training_types: [{ sport_id: SPORT_ID, training_type: 'freeform' }],
      }),
    }),
  );
});
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement**

In the sports section, for each selected sport render a small `p-select` (options: inherit=null, structured, freeform) bound to a local `Map<sportId, TrainingTypeEnum|null>` seeded from the team's `sports[].training_type`. On submit, build `sport_training_types = [...map].filter(...).map(([sport_id, training_type]) => ({ sport_id, training_type }))` and include it in the `patchedTeam`/`team` payload. Labels via `teams.form.training_type_*`.

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add src/app/features/teams/teams-form
git commit -m "feat(training): per-sport training_type override in team form"
```

### Task 17: default_training_type dropdown in sports-form

**Files:**
- Modify: `frontend/src/app/features/admin/sports/sports-form/sports-form.component.{ts,html}`
- Test: `sports-form.component.spec.ts`

- [ ] **Step 1: Write the failing spec**

```typescript
it('submits default_training_type', () => {
  component.form.patchValue({ slug: 's', default_training_type: 'freeform' });
  access(component).submit();
  // create mode → sportsCreate({ sportAdmin: objectContaining({ default_training_type: 'freeform' }) })
  expect(sportsMock.sportsCreate).toHaveBeenCalledWith(
    expect.objectContaining({ sportAdmin: expect.objectContaining({ default_training_type: 'freeform' }) }),
  );
});
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement**

Add `default_training_type: ['structured']` to the form group; add a `p-select` (options structured/freeform, labels `admin.sports.default_training_type` + the type labels) to the template; include it in the create/update payload (it flows through the existing `sportAdmin` payload object).

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add src/app/features/admin/sports/sports-form
git commit -m "feat(training): default_training_type in sport admin form"
```

---

## Phase 8 — Full verification

### Task 18: Full suites + builds

- [ ] **Step 1: Backend full suite**

Run (backend): `.venv\Scripts\python.exe -m pytest -q` → all green.

- [ ] **Step 2: Frontend suite + typecheck + build**

Run (frontend):
- `npx tsc --noEmit -p tsconfig.app.json` → 0
- `npx tsc --noEmit -p tsconfig.spec.json` → 0
- `npx ng test --watch=false` → all green
- `npx ng build --configuration production` → success
- i18n parity: `npx ng test --watch=false` includes `i18n-parity.spec.ts` (must pass).
- `grep -c is_staff src/app/api/model/me.ts` == 1.

- [ ] **Step 3: Commit any final spec fixups, then STOP for deploy review**

Do NOT push until both suites are green and the changes are reviewed. Deploy is push-to-`main` (backend then frontend), each auto-deploys via OIDC→SSM. Watch both deploys (`gh run watch`).

---

## Notes / decisions locked during planning
- `TrainingType` lives in `tools/choices.py` (neutral util module) to avoid `sport`/`team`/`event` → cross-app circular imports.
- Model fields carry `default=STRUCTURED` (Sport, Event) / `null=True` (TeamSport) so the migration backfills existing rows automatically — **no separate data migration** (spec's "two-step non-null" superseded; simpler and equivalent: all existing events end up `structured`).
- Visibility reuses the single `vis_rounds` gate for whichever content is active (rounds OR richtext).
- AI: structured path unchanged; freeform path generates HTML in `Team.language`, sanitized server-side before save.
- Team per-sport override write shape: `sport_training_types: [{sport_id, training_type|null}]` on the team write payload, applied after the existing `_persist_sports`.
- Backlog (separate branch, NOT here): Approach 2 (dedicated `Training` model).
```

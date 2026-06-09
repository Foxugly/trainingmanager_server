# Event training types — design spec

**Status:** 📝 Designed (2026-06-09), not yet implemented.
**Goal:** make an Event's *training content* polymorphic. Today the content is
always the structured model (`Event.rounds` → `Round` → `Exercise`), built for
swimming. We want other sports to use a different content sub-type — initially a
free-form rich-text ("quill"), with room to add more sub-types later. An event has
exactly **one** active content type at a time (mutually exclusive).

This is **Approach 1** (discriminator + co-existing nullable content on `Event`).
The "purer" **Approach 2** (a separate `Training` model with sub-types) is
explicitly **deferred to the backlog** for a later branch — Approach 1 preserves
the entire working structured pipeline (rounds/exercises, reorder, clone, AI
generation, `vis_rounds`) at minimal risk.

## Decisions (agreed during brainstorming)

1. **Training type is an enum** `TrainingType`:
   - `STRUCTURED` — the current `Event.rounds` (→ Round → Exercise) model.
   - `FREEFORM` — a single sanitized rich-text HTML blob.
   - Extensible: adding a future sub-type = one enum value + one content field + one branch.

2. **The effective type is resolved by a 3-level cascade**, then **owned by the event**:
   - `Sport.default_training_type` — global default per sport (admin-editable; default `STRUCTURED`).
   - `TeamSport.training_type` — **nullable** per-team-per-sport override (null = inherit the sport default).
   - `Event.training_type` — **non-null**; seeded at event creation from
     `TeamSport.training_type ?? Sport.default_training_type`, then editable by the coach.
   - The event **stores its own concrete `training_type`** so a later change to a
     sport/team default never retroactively flips an event that already has content.

3. **Single active slot, replaced on switch.** Changing `Event.training_type`
   clears the now-inactive content (leaving `STRUCTURED` → `rounds.clear()`;
   leaving `FREEFORM` → `training_richtext = ""`). The frontend asks for
   confirmation before switching when the current content is non-empty.

4. **AI generation is type-aware.** `POST /events/{id}/generate-training/` passes
   the event's `training_type` to the model: `STRUCTURED` → generates rounds
   (today's behaviour, unchanged); `FREEFORM` → generates rich-text HTML written
   to `training_richtext`. Both types support AI generation. For `FREEFORM`, the
   request also passes the **team's language** (`Team.language`, resolved via
   `event.refer_program.team`) so the generated prose is written in the team's
   language.

5. **Visibility reuses the existing `vis_rounds` gate** for the active training
   content regardless of type (no new visibility flag).

6. **Migration:** every existing event → `STRUCTURED`; every
   `Sport.default_training_type` → `STRUCTURED`.

## Data model

### `sport.Sport`
- `default_training_type = CharField(choices=TrainingType.choices, default=TrainingType.STRUCTURED)`.
  Admin-editable; serialized read-only in the public sport payload and
  read/write in the admin sport serializer.

### `team.TeamSport` (existing through model: already has `is_default`, `order`)
- `training_type = CharField(choices=TrainingType.choices, null=True, blank=True, default=None)`.
  `null` ⇒ inherit `sport.default_training_type`.

### `event.Event`
- `training_type = CharField(choices=TrainingType.choices)` — **non-null**, no DB
  default (always set explicitly at create from the cascade; see "Resolution").
- `training_richtext = TextField(blank=True, default="")` — sanitized HTML for the
  `FREEFORM` content (sanitized with the same bleach config as `Event.goal`).
- `rounds` (existing M2M) — unchanged, the `STRUCTURED` content.

### Enum location
`TrainingType` lives in a shared choices module (e.g. `event/choices.py` or a new
`training/choices.py`); referenced by `sport`, `team`, `event`. Registered in
`SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"]` as `TrainingTypeEnum` so the
generated client enum is stable.

## Type resolution (create-time seeding)

On `Event` create, the serializer/service computes:
```
resolved = TeamSport(team=event.team, sport=event.sport).training_type
           or event.sport.default_training_type   # sport default
           or TrainingType.STRUCTURED             # final fallback
event.training_type = resolved
```
(`event.sport` and the team are already known at create.) The coach may then PATCH
`training_type` to any valid value; the cascade is **only** for the initial seed.

A helper `Event.resolve_default_training_type()` (or a serializer method) computes
the cascade so both create-seeding and the frontend "what would the default be"
hint share one implementation.

## Switch behaviour (single slot)

Implemented in `EventSerializer.update` (or a small `set_training_type` service):
when the incoming `training_type` differs from the stored one, after saving the
new type, clear the inactive content:
- new type ≠ `STRUCTURED` ⇒ `instance.rounds.clear()`.
- new type ≠ `FREEFORM` ⇒ `instance.training_richtext = ""`.

Idempotent and safe if the content is already empty. The destructive clear is
gated in the UI by a confirmation when content exists (the API itself trusts the
authenticated manager).

## API changes

- `EventSerializer`: add `training_type` (read/write) and `training_richtext`
  (read/write, sanitized). `rounds`/`rounds_detail` stay as-is. Add a read-only
  `resolved_training_type_default` hint (optional, for the create form).
- `SportAdminSerializer`: add `default_training_type` (read/write).
  Public `SportSerializer`: expose `default_training_type` read-only.
- `TeamSerializer`: the per-sport override `training_type` is written alongside the
  team's sports. The team sports write path currently takes `sport_ids` (+ a
  `default_sport_id`); extend it to optionally carry a per-sport `training_type`
  map/payload (e.g. `team_sports: [{sport_id, training_type}]` on write, or a
  dedicated nested write). **This is the highest-surface part** — see "Open
  implementation notes".
- `generate-training`: pass `training_type` into the AI request; branch the
  prompt/parsing on it (structured rounds vs HTML body) and write to the matching
  field. For `FREEFORM`, also pass `Team.language` so the generated HTML prose is
  in the team's language.

## Frontend (Angular)

- **Event training area** (`event-training` / its host in `events-detail`): branch
  on `event.training_type`. `STRUCTURED` → the existing rounds UI (`event-training`
  + `round-exercises`). `FREEFORM` → `app-rich-editor` bound to `training_richtext`
  with a save action.
- **Type selector** on the event (coach): a control to change `training_type`,
  with a confirm dialog when switching would clear existing content.
- **Team config** (`teams-form`, sports section): a per-sport training-type
  dropdown (null = "inherit sport default").
- **Sport admin** (`sports-form`): a `default_training_type` dropdown.
- New i18n keys (5 locales) for the type labels, the switch-confirm, and the
  freeform editor heading.
- Regenerate the API client (additive: new enum + fields). Single-request-parameter
  convention unchanged.

## Migration

1. Schema migration: add the three fields (`Sport.default_training_type`,
   `TeamSport.training_type`, `Event.training_type`, `Event.training_richtext`).
   `Event.training_type` is added **non-null with a temporary default** then the
   default is dropped (two-step) — or added with a data migration that backfills
   before enforcing non-null.
2. Data migration: set all `Sport.default_training_type = STRUCTURED` and all
   `Event.training_type = STRUCTURED`.

## Testing

- **Cascade resolution:** sport default only; team override beats sport default;
  event create seeds from the cascade; event override beats both.
- **Switch clears content:** STRUCTURED→FREEFORM clears rounds; FREEFORM→STRUCTURED
  clears `training_richtext`; no-op when type unchanged.
- **FREEFORM content:** richtext is sanitized (bleach) on write; XSS payload stripped.
- **AI generation:** structured event → rounds produced (existing tests stay green);
  freeform event → `training_richtext` populated, rounds untouched.
- **Visibility:** `vis_rounds` gate applies to the active content for both types.
- **Permissions:** only managers can change `training_type` / write either content
  (existing event-mutation perms, unchanged).
- **Migration:** existing events end up `STRUCTURED` with their rounds intact.
- Frontend: branch rendering by type; confirm-on-switch; team/sport override controls.

## Open implementation notes

- **Team per-sport override write path** is the fiddliest piece (the team serializer
  currently writes sports as a flat id list). The implementation plan should pick
  the concrete shape (nested `team_sports` write objects vs a side map) and keep it
  consistent with the existing `sport_ids`/`default_sport_id` handling. If it proves
  heavy, the team-level override can ship as a fast-follow after sport-default +
  event-override (the cascade already degrades gracefully: null team override →
  sport default).
- **AI freeform prompt/parsing**: define the freeform generation contract (HTML
  shape, length bounds, target language = `Team.language`) in the plan; reuse the
  existing Anthropic tool-use harness.

## Future / backlog (NOT in this spec)

- **Approach 2** — extract training content into a dedicated `Training` model
  (OneToOne with Event) with proper sub-types, on its own branch, if/when the number
  of training sub-types grows enough to justify the refactor.
- AI generation for additional future sub-types.

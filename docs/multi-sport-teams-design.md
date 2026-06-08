# Multi-sport teams — design spec

**Status:** ✅ Implemented & deployed (2026-06-08) — all 6 phases shipped (backend
`44a6ef7`…`f08749c`, frontend `a826e79`+`cb25e7c`). This is the as-designed spec;
see "As-built notes" at the end for where the implementation diverged.
**Goal:** a Team can practise several sports (M2M) with one default sport, instead
of a single `Team.sport` FK. Sport stays the central scoping axis, now plural.

## Decisions (agreed)

1. **Team.sport (FK) → Team.sports (M2M via a `TeamSport` through model).**
   The default sport is a **`is_default` boolean on the through row** (NOT a
   separate `default_sport` FK). Exactly one `TeamSport` per team has
   `is_default=True` (enforced by a partial unique constraint:
   `UniqueConstraint(fields=["team"], condition=Q(is_default=True))`). A
   `Team.default_sport` **property** returns that flagged sport (or None).
   `TeamSport` may also carry `order` for display.

   > "Union" (used throughout below) just means *set union*: a team that does
   > {Swimming, Running} sees Swimming's catalog **plus** Running's catalog
   > merged together (no duplicates). A single-sport team sees no change.
2. **Each session picks its sport: new `Event.sport` (FK)** — one of the event's
   team's sports; default = `team.default_sport`. Drives AI generation + which
   modalities apply to that session.
3. **Catalog (Modality / Round / Exercise) stays mono-sport per row.** A team
   *sees* the **union** of its sports × its language. A Round/Exercise still has
   one sport and the modality.sport == round.sport invariant is unchanged.
4. **Place.sport (FK) → Place.sports (M2M)** — a venue can serve several sports.
   A team's place pool = places sharing ≥1 of the team's sports.
5. **Equipment stays mono-sport** (FK to one sport). A team's equipment pool =
   union over its sports (read-side only).
6. **Plan AI sport comes from the training template:** add **`TrainingSlot.sport`
   (FK)**. The weekly template carries a sport per slot; `generate-events`
   stamps each generated `Event.sport` from its slot's sport.
7. **Frontend sport tab = an M2M+default selector**, mirroring the places/managers
   editor pattern (no Nominatim, options from the global sport catalog).

## Model deltas (+ data migrations, all backfilled from the current FK)

| Model | Change | Backfill |
|---|---|---|
| `Team` | drop `sport` FK; add `sports` M2M(Sport, through=`TeamSport`); `TeamSport(team, sport, is_default, order)` | one `TeamSport` row per team: `sport = old sport`, `is_default = True` |
| `Event` | add `sport` FK(Sport, null) | `sport = refer_program.team.<old sport>` for existing events |
| `Place` | drop `sport` FK; add `sports` M2M(Sport) | `sports = {old sport}` |
| `TrainingSlot` | add `sport` FK(Sport, null) | `sport = team.<old sport>` for existing slots |
| `Equipment` | unchanged | — |
| `Round`/`Exercise` | unchanged | — |

> Migrations are 2-step where a column is replaced (add new → data migrate →
> drop old) to keep prod safe. Each model migration ships with its backfill.

## Scoping rules (the heart of the change)

**Two distinct layers — do not conflate them:**

- **Access / authorization scope = UNION.** What a user is *allowed* to reach.
  A coach of a {Swimming, Running} team may access both catalogs (else they
  couldn't build sessions for both). This is the security filter
  (`scope_by_sport_language`, `user_accessible_sport_language_pairs`): it
  iterates over **all** sports of the user's teams (was the single team sport).
- **Context filter = SINGLE sport (`event.sport`).** Inside a specific session,
  the round picker, the exercise editor's **modality** select, AND the AI
  generator are all filtered to **that event's sport only** — NOT the union.
  Building a Swimming event proposes only Swimming rounds, exercises and
  modalities. The event endpoints pass `?sport=event.sport` (or the AI scopes
  modalities by it) on top of the union access scope.

So: union *authorizes*, `event.sport` *filters* within a session. Same for any
other single-sport context (e.g. picking a round for a specific event).

- `team/queries` + `team/utils`: `user_accessible_sport_language_pairs` and
  `scope_by_sport_language` iterate over **all** sports of the user's teams
  (the union access scope). The team's library list = union; an event-context
  list adds the `event.sport` filter.
- **Place pool** for a team: `Place.objects.filter(sports__in=team.sports.all())`.
  Place create links the chosen sports (candidates = the team's sports).
- **Equipment pool** for a team: `Equipment.objects.filter(sport__in=team.sports.all())`.
- **AI generate-training** (single event): modalities/energysegments scoped by
  `event.sport` (fallback `team.default_sport`).
- **AI generate-events** (plan): each generated event's `sport` = its slot's
  `TrainingSlot.sport` (fallback `default_sport`).
- Everywhere a single sport is still needed for a team out of context (labels,
  fallbacks): use `team.default_sport`.

## Ripple (known touch points)

Backend (~13 `team.sport` reads): `place/views`, `program/views` (+ `program/ai`),
`event/ai`, `event/views`, `round/*`, `team/serializers`, `team/utils`,
`team/queries`, the catalog-scoping helpers, plus all serializers exposing
`sport`/`sport_id`. New: `Event.sport`, `TrainingSlot.sport`, `Place.sports`,
`Team.sports`/`default_sport` on their serializers + OpenAPI.

Frontend: teams-form sport tab (new `app-team-sports`, M2M+default), training-slots
editor (add a per-row sport select — extends the just-extracted
`app-team-slots-editor`), event-form sport select, generate-events flow, and every
read of `team.sport` (teams-list, dashboard, events, programs…). Full `api:gen`.

## Phased plan (each phase: tests + deploy, behaviour-preserving where possible)

1. **Team.sports (TeamSport through, is_default)** (+ backfill). Add a
   `Team.default_sport` property (the is_default sport). Internally read
   `default_sport` everywhere `sport` was read → functional no-op. Serializer
   exposes nested `sports` (each `{sport, is_default, order}`); writes via
   `sport_ids` + a `default_sport_id` (the one to flag), or a dedicated
   set-default action. Keep a read-only `sport` alias = default during the
   transition if useful.
2. **Union scoping**: catalog (rounds/exercises/modalities) + place + equipment
   pools span all the team's sports. Read-only change; writes still mono-sport.
3. **Event.sport** + AI generate-training scoped by it; event-form sport select;
   the event builder's round/exercise pickers filter by `event.sport` (single),
   on top of the union access scope.
4. **TrainingSlot.sport** + slots-editor per-row sport + generate-events stamping.
5. **Place.sports M2M**.
6. **Frontend**: sport tab (M2M+default), all `team.sport` reads, `api:gen`.

## Open points / risks

- `Team.name` uniqueness + sport were sometimes used together; verify no unique
  constraint couples sport.
- `Round.sport`/`Exercise` unchanged, but a multi-sport team's UI must let the
  coach pick which sport when creating a round/exercise (currently implicit from
  team.sport) — confirm UX in phase 2/3.
- Drop-column migrations on prod: do add+backfill+drop across two deploys if we
  want zero-downtime safety; acceptable to do single-deploy given low traffic.
- A team could have `sports=[]` (mirrors today's nullable sport) → no
  `is_default` row, so `default_sport` property is None. Guards needed where
  AI/catalog assume a sport.
- Enforce exactly-one-default: partial unique constraint on `is_default=True`
  per team + serializer/save logic that flips the flag atomically when the
  default changes (and auto-sets the first sport as default when adding the
  first one).

## As-built notes (what shipped vs this spec)

- **Team API surface:** `TeamSerializer` exposes `sports` (read, flattened
  `TeamSportRead` with `is_default`/`order`), `sport_ids` (write, replaces the
  set) and `default_sport_id` (write). The legacy `sport`/`sport_id` shim stays
  for back-compat; `sport_id` is now optional (a team still needs ≥1 sport via
  either path). Owner-only: `managers_ids`; venues are scoped to a team sport
  (`place_not_in_sport`).
- **Frontend sport tab:** implemented **inline in `teams-form`** (a `sport_ids`
  `p-multiSelect` + a `default_sport_id` `p-select`), not as a dedicated
  `app-team-sports` component. The slots editor has a per-row sport column and
  the event form a session-sport select (both shown only when the team has >1
  sport). Event detail shows the session sport as a badge.
- **Scoping in practice:** access = union of the team's sports
  (`user_accessible_sport_language_pairs`, `Place.sports` union pool); context
  filter = `event.sport` (AI generate-training + the event-detail round/exercise
  pickers scope to it). Equipment stayed mono-sport (team-enabled set).
- **Exactly-one-default** is enforced by the `uniq_default_sport_per_team`
  partial unique constraint + `_sync_sports`/`_apply_default_sport` in the
  serializer (clears all default flags before setting the new one to avoid a
  mid-update clash).

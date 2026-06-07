"""Shared, model-agnostic backfill logic for managed Places.

Kept here (rather than inline in the data migration) so it can be exercised
directly by tests against the live models, while the migration calls it with
historical models via ``apps.get_model``. The function only ever creates
Places and sets the additive ``place`` / ``default_place`` FKs — it never
touches the canonical ``location`` / ``default_pool`` strings, and is
idempotent.
"""


def backfill_places(Team, Event, Place):
    """Derive managed Places from existing free-text locations.

    For each Team: create a Place for every distinct non-empty
    ``Event.location`` among the team's events and link matching events'
    ``place`` FK; if ``Team.default_pool`` is set, get_or_create the matching
    Place and set ``default_place``.

    The model classes are passed in so the data migration can hand over
    historical models and tests can hand over the live ones.
    """
    for team in Team.objects.all().iterator():
        locations = (
            Event.objects.filter(refer_program__team_id=team.id)
            .exclude(location="")
            .exclude(location__isnull=True)
            .values_list("location", flat=True)
            .distinct()
        )
        name_to_place = {}
        for raw in locations:
            name = (raw or "").strip()
            if not name or name in name_to_place:
                continue
            place, _created = Place.objects.get_or_create(team_id=team.id, name=name)
            name_to_place[name] = place

        for name, place in name_to_place.items():
            Event.objects.filter(
                refer_program__team_id=team.id, location=name, place__isnull=True
            ).update(place=place)

        default_pool = (getattr(team, "default_pool", "") or "").strip()
        if default_pool and team.default_place_id is None:
            place = name_to_place.get(default_pool)
            if place is None:
                place, _created = Place.objects.get_or_create(
                    team_id=team.id, name=default_pool
                )
            team.default_place = place
            team.save(update_fields=["default_place"])

"""Soft backfill: derive managed Places from existing free-text locations.

For each Team, create a Place for every DISTINCT non-empty Event.location
among that team's events and link the matching events' ``place`` FK to it.
Also, if ``Team.default_pool`` is non-empty, get_or_create the matching Place
and set ``Team.default_place``.

Additive and idempotent: it never touches the canonical ``location`` /
``default_pool`` strings, skips blanks, and re-running it is a no-op once the
places exist and the FKs are set. Uses historical models via apps.get_model.
"""

from django.db import migrations


def backfill_places(apps, schema_editor):
    from place.services import backfill_places as run

    Team = apps.get_model("team", "Team")
    Event = apps.get_model("event", "Event")
    Place = apps.get_model("place", "Place")
    run(Team, Event, Place)


def noop_reverse(apps, schema_editor):
    # Non-destructive forward migration; reversing is a no-op (we never drop
    # the canonical location/default_pool strings, and Places can be removed
    # via normal CRUD if desired).
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("place", "0001_initial"),
        ("event", "0010_event_place"),
        ("team", "0018_team_default_place"),
    ]

    operations = [
        migrations.RunPython(backfill_places, noop_reverse),
    ]

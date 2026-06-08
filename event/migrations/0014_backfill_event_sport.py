"""Backfill Event.sport from the event's team default sport.

Each event linked to a program inherits its team's default sport (the
is_default TeamSport). Idempotent: only fills events whose sport is still NULL.
"""

from django.db import migrations


def backfill(apps, schema_editor):
    Event = apps.get_model("event", "Event")
    TeamSport = apps.get_model("team", "TeamSport")
    defaults = dict(
        TeamSport.objects.filter(is_default=True).values_list("team_id", "sport_id")
    )
    for event in (
        Event.objects.filter(refer_program__isnull=False, sport__isnull=True)
        .select_related("refer_program")
    ):
        sport_id = defaults.get(event.refer_program.team_id)
        if sport_id is not None:
            event.sport_id = sport_id
            event.save(update_fields=["sport"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("event", "0013_event_sport"),
        ("team", "0025_remove_team_sport_alter_team_sports"),
    ]

    operations = [migrations.RunPython(backfill, noop)]

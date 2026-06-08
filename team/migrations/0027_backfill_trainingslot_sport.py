"""Backfill TrainingSlot.sport from the slot's team default sport.

Each existing weekly slot inherits its team's default sport (the is_default
TeamSport). Idempotent: only fills slots whose sport is still NULL.
"""

from django.db import migrations


def backfill(apps, schema_editor):
    TrainingSlot = apps.get_model("team", "TrainingSlot")
    TeamSport = apps.get_model("team", "TeamSport")
    defaults = dict(
        TeamSport.objects.filter(is_default=True).values_list("team_id", "sport_id")
    )
    for slot in TrainingSlot.objects.filter(sport__isnull=True).only("id", "team_id"):
        sport_id = defaults.get(slot.team_id)
        if sport_id is not None:
            slot.sport_id = sport_id
            slot.save(update_fields=["sport"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("team", "0026_trainingslot_sport"),
    ]

    operations = [migrations.RunPython(backfill, noop)]

"""Backfill Place.sports (M2M) from the legacy single Place.sport FK.

Each place with a sport gets that sport in its new M2M. Idempotent.
"""

from django.db import migrations


def backfill(apps, schema_editor):
    Place = apps.get_model("place", "Place")
    for place in Place.objects.exclude(sport__isnull=True).only("id", "sport_id"):
        place.sports.add(place.sport_id)


def unbackfill(apps, schema_editor):
    Place = apps.get_model("place", "Place")
    for place in Place.objects.all().only("id"):
        place.sports.clear()


class Migration(migrations.Migration):
    dependencies = [
        ("place", "0006_place_sports"),
    ]

    operations = [migrations.RunPython(backfill, unbackfill)]

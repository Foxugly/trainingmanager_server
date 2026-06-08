"""Backfill TeamSport from the legacy Team.sport FK.

Each team that had a single sport gets one TeamSport row flagged is_default.
Idempotent (get_or_create) so it is safe to re-run.
"""

from django.db import migrations


def backfill(apps, schema_editor):
    Team = apps.get_model("team", "Team")
    TeamSport = apps.get_model("team", "TeamSport")
    for team in Team.objects.exclude(sport__isnull=True).only("id", "sport_id"):
        TeamSport.objects.get_or_create(
            team_id=team.id,
            sport_id=team.sport_id,
            defaults={"is_default": True, "order": 0},
        )


def unbackfill(apps, schema_editor):
    TeamSport = apps.get_model("team", "TeamSport")
    TeamSport.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("team", "0023_teamsport_team_sports_teamsport_uniq_team_sport_and_more"),
    ]

    operations = [migrations.RunPython(backfill, unbackfill)]

from django.db import migrations


def forward(apps, schema_editor):
    """Copy each place's owning team into the new Team.places M2M and set the
    place's sport from that team's sport, before the team FK is dropped."""
    Place = apps.get_model("place", "Place")
    for place in Place.objects.select_related("team", "team__sport").all():
        if place.team_id is None:
            continue
        if place.sport_id is None and place.team.sport_id is not None:
            place.sport_id = place.team.sport_id
            place.save(update_fields=["sport"])
        place.team.places.add(place)


def backward(apps, schema_editor):
    """Best-effort reverse: restore team from the first linked team."""
    Place = apps.get_model("place", "Place")
    for place in Place.objects.prefetch_related("teams").all():
        first = place.teams.first()
        if first is not None:
            place.team_id = first.id
            place.save(update_fields=["team"])


class Migration(migrations.Migration):

    dependencies = [
        ("place", "0003_remove_place_uniq_place_team_name_place_sport_and_more"),
        ("team", "0020_team_places_alter_team_default_place"),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]

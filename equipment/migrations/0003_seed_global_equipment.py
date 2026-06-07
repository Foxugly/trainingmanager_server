from django.db import migrations

# Global, multilingual training-equipment catalog for the Natation sport.
# Each row: per-language names (fr, nl, en, it, es). The base ``name`` column is
# set to the default-language (fr) value to match modeltranslation's convention.
SEED = [
    {"fr": "Pull-buoy", "nl": "Pull-buoy", "en": "Pull buoy", "it": "Pull buoy", "es": "Pull buoy"},
    {"fr": "Plaquettes", "nl": "Handpeddels", "en": "Paddles", "it": "Palette", "es": "Palas"},
    {"fr": "Palmes", "nl": "Zwemvliezen", "en": "Fins", "it": "Pinne", "es": "Aletas"},
    {"fr": "Planche", "nl": "Plankje", "en": "Kickboard", "it": "Tavoletta", "es": "Tabla"},
    {"fr": "Tuba frontal", "nl": "Snorkel", "en": "Snorkel", "it": "Boccaglio frontale", "es": "Tubo frontal"},
    {"fr": "Élastique", "nl": "Elastiek", "en": "Resistance band", "it": "Elastico", "es": "Banda elástica"},
    {"fr": "Parachute de traction", "nl": "Weerstandsparachute", "en": "Drag parachute", "it": "Paracadute", "es": "Paracaídas"},
    {"fr": "Chronomètre", "nl": "Stopwatch", "en": "Stopwatch", "it": "Cronometro", "es": "Cronómetro"},
]


def seed(apps, schema_editor):
    Equipment = apps.get_model("equipment", "Equipment")
    Sport = apps.get_model("sport", "Sport")
    # The previous (team-scoped) rows were QA-only; clear them so the catalog is
    # purely the curated global list. Cascades clean the team/event M2M links.
    Equipment.objects.all().delete()
    natation = Sport.objects.filter(slug="natation").first()
    for row in SEED:
        Equipment.objects.create(
            sport=natation,
            name=row["fr"],
            name_fr=row["fr"],
            name_nl=row["nl"],
            name_en=row["en"],
            name_it=row["it"],
            name_es=row["es"],
            is_active=True,
        )


def unseed(apps, schema_editor):
    Equipment = apps.get_model("equipment", "Equipment")
    Equipment.objects.filter(name__in=[r["fr"] for r in SEED]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("equipment", "0002_remove_equipment_uniq_equipment_team_name_and_more"),
        ("team", "0019_team_equipment"),
        ("event", "0011_event_equipment_items"),
        ("sport", "0006_seed_default_sport"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]

"""Seed the seven default team skill Levels (discovery -> excellence).

Names and descriptions are populated for the 5 supported languages directly
into the modeltranslation columns (name_fr/nl/en/it/es,
description_fr/nl/en/it/es) and the canonical name/description (default to
French, the project default). Idempotent via code-based update_or_create.
"""

from django.db import migrations

DEFAULT_LEVELS = [
    {
        "code": "discovery",
        "order": 1,
        "name": "Découverte",
        "name_fr": "Découverte",
        "name_nl": "Ontdekking",
        "name_en": "Discovery",
        "name_it": "Scoperta",
        "name_es": "Descubrimiento",
        "description": "Découvre l'activité et apprend les règles de base",
        "description_fr": "Découvre l'activité et apprend les règles de base",
        "description_nl": "Ontdekt de activiteit en leert de basisregels",
        "description_en": "Discovers the activity and learns the basic rules",
        "description_it": "Scopre l'attività e impara le regole di base",
        "description_es": "Descubre la actividad y aprende las reglas básicas",
    },
    {
        "code": "initiation",
        "order": 2,
        "name": "Initiation",
        "name_fr": "Initiation",
        "name_nl": "Initiatie",
        "name_en": "Initiation",
        "name_it": "Iniziazione",
        "name_es": "Iniciación",
        "description": "Acquiert les fondamentaux techniques, pratique encadrée",
        "description_fr": "Acquiert les fondamentaux techniques, pratique encadrée",
        "description_nl": "Verwerft de technische grondbeginselen, begeleide oefening",
        "description_en": "Acquires the technical fundamentals, supervised practice",
        "description_it": "Acquisisce i fondamentali tecnici, pratica supervisionata",
        "description_es": "Adquiere los fundamentos técnicos, práctica supervisada",
    },
    {
        "code": "development",
        "order": 3,
        "name": "Développement",
        "name_fr": "Développement",
        "name_nl": "Ontwikkeling",
        "name_en": "Development",
        "name_it": "Sviluppo",
        "name_es": "Desarrollo",
        "description": "Consolidation des acquis, début de l'autonomie",
        "description_fr": "Consolidation des acquis, début de l'autonomie",
        "description_nl": "Consolidatie van vaardigheden, begin van autonomie",
        "description_en": "Consolidating skills, beginning of autonomy",
        "description_it": "Consolidamento delle competenze, inizio dell'autonomia",
        "description_es": "Consolidación de las competencias, inicio de la autonomía",
    },
    {
        "code": "perfection",
        "order": 4,
        "name": "Perfectionnement",
        "name_fr": "Perfectionnement",
        "name_nl": "Vervolmaking",
        "name_en": "Refinement",
        "name_it": "Perfezionamento",
        "name_es": "Perfeccionamiento",
        "description": "Maîtrise des techniques principales, compréhension tactique et stratégique",
        "description_fr": (
            "Maîtrise des techniques principales, compréhension tactique et stratégique"
        ),
        "description_nl": (
            "Beheersing van de belangrijkste technieken, tactisch en strategisch inzicht"
        ),
        "description_en": "Mastery of the main techniques, tactical and strategic understanding",
        "description_it": (
            "Padronanza delle tecniche principali, comprensione tattica e strategica"
        ),
        "description_es": (
            "Dominio de las técnicas principales, comprensión táctica y estratégica"
        ),
    },
    {
        "code": "competition",
        "order": 5,
        "name": "Compétition",
        "name_fr": "Compétition",
        "name_nl": "Competitie",
        "name_en": "Competition",
        "name_it": "Competizione",
        "name_es": "Competición",
        "description": "Participation régulière aux compétitions, recherche de performance",
        "description_fr": "Participation régulière aux compétitions, recherche de performance",
        "description_nl": "Regelmatige deelname aan competities, streven naar prestatie",
        "description_en": "Regular competition, pursuit of performance",
        "description_it": "Partecipazione regolare alle competizioni, ricerca della prestazione",
        "description_es": "Participación regular en competiciones, búsqueda del rendimiento",
    },
    {
        "code": "performance",
        "order": 6,
        "name": "Niveau régional ou national",
        "name_fr": "Niveau régional ou national",
        "name_nl": "Regionaal of nationaal niveau",
        "name_en": "Regional or national level",
        "name_it": "Livello regionale o nazionale",
        "name_es": "Nivel regional o nacional",
        "description": "Entraînement structuré et objectifs sportifs",
        "description_fr": "Entraînement structuré et objectifs sportifs",
        "description_nl": "Gestructureerde training en sportieve doelstellingen",
        "description_en": "Structured training and sporting goals",
        "description_it": "Allenamento strutturato e obiettivi sportivi",
        "description_es": "Entrenamiento estructurado y objetivos deportivos",
    },
    {
        "code": "excellence",
        "order": 7,
        "name": "Excellence",
        "name_fr": "Excellence",
        "name_nl": "Excellentie",
        "name_en": "Excellence",
        "name_it": "Eccellenza",
        "name_es": "Excelencia",
        "description": "Haut niveau, niveau national élite ou international",
        "description_fr": "Haut niveau, niveau national élite ou international",
        "description_nl": "Hoog niveau, nationaal eliteniveau of internationaal",
        "description_en": "High level, national elite or international",
        "description_it": "Alto livello, livello nazionale élite o internazionale",
        "description_es": "Alto nivel, nivel nacional de élite o internacional",
    },
]


def seed_levels(apps, schema_editor):
    Level = apps.get_model("level", "Level")
    for data in DEFAULT_LEVELS:
        Level.objects.update_or_create(code=data["code"], defaults=data)
    print(f"  Seeded {len(DEFAULT_LEVELS)} default Level")


def reverse_seed(apps, schema_editor):
    # Don't auto-delete — levels may be linked to user-created teams.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("level", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_levels, reverse_seed),
    ]

from django.db import migrations, models
from django.db.models import Count
from django.utils import timezone


def dedupe_active_memberships(apps, schema_editor):
    """Close (set left_at) any duplicate ACTIVE memberships before the partial
    unique constraint is added, so the constraint creation can't fail on
    pre-existing duplicates (the accept-race bug this migration guards against).
    Keeps the earliest-joined row of each (team, member) group active.
    """
    TeamMembership = apps.get_model("team", "TeamMembership")
    now = timezone.now()
    dupes = (
        TeamMembership.objects.filter(left_at__isnull=True)
        .values("team_id", "member_id")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
    )
    for d in dupes:
        rows = list(
            TeamMembership.objects.filter(
                team_id=d["team_id"], member_id=d["member_id"], left_at__isnull=True
            ).order_by("joined_at", "id")
        )
        for extra in rows[1:]:
            extra.left_at = now
            extra.save(update_fields=["left_at"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("team", "0028_teamsport_training_type"),
    ]

    operations = [
        migrations.RunPython(dedupe_active_memberships, noop),
        migrations.AddConstraint(
            model_name="teammembership",
            constraint=models.UniqueConstraint(
                condition=models.Q(left_at__isnull=True),
                fields=("team", "member"),
                name="uniq_active_membership_per_team_member",
            ),
        ),
    ]

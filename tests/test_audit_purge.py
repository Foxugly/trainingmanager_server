"""Coverage of the purge_audit_log retention command."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from audit.models import AuditAction, AuditLogEntry
from audit.services import record
from tests.factories import TeamFactory

pytestmark = pytest.mark.django_db


def _entry(team, *, age_days):
    """Create an audit row then back-date created_at (it's auto_now_add)."""
    e = AuditLogEntry.objects.create(
        action=AuditAction.MEMBER_ANONYMIZED, team=team, target_repr="x"
    )
    AuditLogEntry.objects.filter(pk=e.pk).update(
        created_at=timezone.now() - timedelta(days=age_days)
    )
    return e


def test_purge_deletes_old_keeps_recent():
    team = TeamFactory()
    old = _entry(team, age_days=400)
    recent = _entry(team, age_days=10)

    call_command("purge_audit_log", days=365)

    assert not AuditLogEntry.objects.filter(pk=old.pk).exists()
    assert AuditLogEntry.objects.filter(pk=recent.pk).exists()


def test_purge_dry_run_deletes_nothing():
    team = TeamFactory()
    old = _entry(team, age_days=400)

    call_command("purge_audit_log", days=365, dry_run=True)

    assert AuditLogEntry.objects.filter(pk=old.pk).exists()


def test_purge_is_idempotent():
    team = TeamFactory()
    _entry(team, age_days=400)

    call_command("purge_audit_log", days=365)
    # Second run finds nothing to delete and exits cleanly.
    call_command("purge_audit_log", days=365)

    assert AuditLogEntry.objects.filter(created_at__lt=timezone.now() - timedelta(days=365)).count() == 0


def test_purge_batched_deletes_all_old_rows():
    team = TeamFactory()
    for _ in range(5):
        _entry(team, age_days=400)

    call_command("purge_audit_log", days=365, batch_size=2)

    assert AuditLogEntry.objects.count() == 0


def test_purge_keeps_subscription_bypass_entries():
    """Les octrois d'acces offert ont une valeur commerciale : ils survivent a la purge."""
    old = timezone.now() - timedelta(days=800)
    kept = record(AuditAction.SUBSCRIPTION_BYPASS_GRANTED, target_repr="User #1 (a@b.c)")
    purged = record(AuditAction.MEMBER_REMOVED, target_repr="Member #1 (X)")
    AuditLogEntry.objects.filter(pk__in=[kept.pk, purged.pk]).update(created_at=old)

    call_command("purge_audit_log")

    assert AuditLogEntry.objects.filter(pk=kept.pk).exists()
    assert not AuditLogEntry.objects.filter(pk=purged.pk).exists()

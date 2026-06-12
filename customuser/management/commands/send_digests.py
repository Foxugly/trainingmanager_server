"""Send the daily notification digest to users who opted into digest_email.

A digest_email user receives NO immediate notification emails (suppressed in
notifications.services); instead this daily cron emails them one summary of the
in-app notifications created since their last digest. The localized email lists
each notification's title + body + deep link.

Window: notifications created after ``user.last_digest_at`` (or all the user's
unread/recent rows the first time). When there is nothing new, no email is sent
and ``last_digest_at`` is left untouched so nothing is ever missed. After a
successful send, ``last_digest_at`` is advanced to now.

--dry-run computes and logs without sending or advancing the marker.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import get_connection, send_mail
from django.core.management.base import BaseCommand
from django.utils import timezone, translation
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Email a daily digest of notifications to digest_email users (daily cron)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute and log the digests but do not send or advance the marker.",
        )

    def handle(self, *args, **options):
        from notifications.models import Notification

        dry_run = options["dry_run"]
        User = get_user_model()
        now = timezone.now()

        users = User.objects.filter(digest_email=True).exclude(email="")
        sent = 0
        connection = None
        if not dry_run:
            try:
                connection = get_connection(fail_silently=False)
                connection.open()
            except Exception:
                logger.exception("Failed to open the digest email connection")
                connection = None

        try:
            for user in users:
                qs = Notification.objects.filter(recipient=user)
                if user.last_digest_at is not None:
                    qs = qs.filter(created_at__gt=user.last_digest_at)
                rows = list(qs.order_by("created_at"))
                if not rows:
                    continue

                if dry_run:
                    logger.info("[dry-run] would digest %d notif(s) to %s", len(rows), user.email)
                    continue

                body = _build_digest_body(user, rows)
                lang = user.language or "en"
                try:
                    with translation.override(lang):
                        subject = f"[TrainingManager] {_('Your daily digest')}"
                        send_mail(
                            subject=str(subject),
                            message=body,
                            from_email=settings.DEFAULT_FROM_EMAIL,
                            recipient_list=[user.email],
                            fail_silently=False,
                            connection=connection,
                        )
                    # Advance the marker only on a successful send.
                    user.last_digest_at = now
                    user.save(update_fields=["last_digest_at"])
                    sent += 1
                except Exception:
                    logger.exception("Failed to send digest to %s", user.email)
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    logger.exception("Failed to close the digest email connection")

        if dry_run:
            logger.info("Digest dry-run complete.")
        else:
            logger.info("Digest complete: %d email(s) sent.", sent)


def _build_digest_body(user, rows):
    """Localized plain-text digest body. Call inside a translation.override."""
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    greeting = user.first_name or user.email
    lines = [
        f"{_('Hello')} {greeting},",
        "",
        _("Here is a summary of your recent notifications:"),
        "",
    ]
    for n in rows:
        lines.append(f"• {n.title}")
        if n.body:
            lines.append(f"  {n.body}")
        if n.url:
            link = f"{base}{n.url if n.url.startswith('/') else '/' + n.url}"
            lines.append(f"  {link}")
        lines.append("")
    lines.append(
        _("You receive a daily digest instead of per-event emails. Change this in "
          "your notification preferences.")
    )
    return "\n".join(lines)

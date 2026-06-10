"""Server-rendered one-click RSVP from the session-reminder email.

The reminder email carries three signed links (Going / Maybe / Not going),
each a ``GET /api/v1/rsvp-magic/<token>/``. The GET only *renders a
confirmation page* — a link-prefetcher (Outlook safe-links, Gmail bots)
hitting it cannot change anything. The page's POST button performs the
actual upsert. Token = the whole credential (no DB row, 72h TTL); see
:mod:`rsvp.magic_rsvp`.

These are plain Django HTML views (not DRF): the athlete clicking from an
email is unauthenticated, the token authorizes the single action, and there
is no SPA route for it. Excluded from the OpenAPI schema.
"""

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.html import escape
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from event.models import Event

from .magic_rsvp import SignatureExpired, parse_token
from .models import Rsvp, RsvpStatus
from .views import _team_for_event

_STATUS_LABELS = {
    RsvpStatus.GOING: _("Going"),
    RsvpStatus.MAYBE: _("Maybe"),
    RsvpStatus.NOT_GOING: _("Not going"),
}


def _page(title: str, body: str, status: int = 200) -> HttpResponse:
    """Wrap a body fragment in a minimal self-contained HTML document.

    No external CSS/JS — this page is reached straight from an email client,
    often outside the SPA origin, so everything is inline."""
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{escape(title)}</title>
<style>
  body {{ font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
         background: #f3f4f6; color: #111827; margin: 0; padding: 2rem 1rem; }}
  .card {{ max-width: 28rem; margin: 2rem auto; background: #fff; border-radius: 12px;
          box-shadow: 0 1px 3px rgba(0,0,0,.1); padding: 1.75rem; }}
  h1 {{ font-size: 1.25rem; margin: 0 0 .75rem; }}
  p {{ line-height: 1.5; margin: .5rem 0; }}
  .muted {{ color: #6b7280; font-size: .9rem; }}
  button {{ font: inherit; font-weight: 600; color: #fff; background: #059669;
           border: 0; border-radius: 8px; padding: .65rem 1.25rem; cursor: pointer; }}
  button:hover {{ background: #047857; }}
  .ok {{ color: #059669; }}
  .err {{ color: #b91c1c; }}
</style>
</head>
<body><div class="card">{body}</div></body>
</html>"""
    return HttpResponse(html, status=status, content_type="text/html; charset=utf-8")


def _error_page(message: str, status: int = 400) -> HttpResponse:
    body = f'<h1 class="err">{escape(_("RSVP link"))}</h1><p>{escape(message)}</p>'
    return _page(_("RSVP link"), body, status=status)


def _resolve(token: str):
    """Return ``(event, member_id, status, team)`` for a valid, actionable token,
    or an :class:`HttpResponse` error page to short-circuit on. Raises nothing —
    expired/invalid/disabled/non-member all map to a rendered page."""
    try:
        parsed = parse_token(token)
    except SignatureExpired:
        return _error_page(
            _("This RSVP link has expired. Open the app to set your availability."),
            status=410,
        )
    if parsed is None:
        return _error_page(_("This RSVP link is invalid."), status=400)

    event_id, member_id, status = parsed
    event = get_object_or_404(Event, pk=event_id)
    team = _team_for_event(event)
    if team is None:
        return _error_page(_("This event is no longer available."), status=404)
    if not team.rsvp_enabled:
        return _error_page(_("Availability (RSVP) is turned off for this team."), status=409)
    if not team.memberships.filter(member_id=member_id, left_at__isnull=True).exists():
        return _error_page(_("You are no longer an active member of this team."), status=403)
    return event, member_id, status, team


@csrf_exempt
@require_http_methods(["GET", "POST"])
def rsvp_magic(request, token: str) -> HttpResponse:
    """GET renders a confirmation page; POST performs the RSVP upsert.

    CSRF-exempt because the signed token (unguessable, athlete-scoped, single
    action) is the credential and the page is reached cross-origin from an
    email client with no session cookie."""
    resolved = _resolve(token)
    if isinstance(resolved, HttpResponse):
        return resolved
    event, member_id, status, team = resolved

    status_label = _STATUS_LABELS.get(status, status)
    event_name = event.name
    when = ""
    if event.date:
        when = event.date.strftime("%Y-%m-%d")
        if event.hour_start:
            when += " " + event.hour_start.strftime("%H:%M")

    if request.method == "GET":
        body = (
            f'<h1>{escape(_("Confirm your availability"))}</h1>'
            f"<p>{escape(_('Event'))}: <strong>{escape(event_name)}</strong></p>"
            + (f'<p class="muted">{escape(when)}</p>' if when else "")
            + f"<p>{escape(_('Your response'))}: <strong>{escape(str(status_label))}</strong></p>"
            f'<form method="post">'
            f'<button type="submit">{escape(_("Confirm"))}</button>'
            f"</form>"
        )
        return _page(_("Confirm your availability"), body)

    # POST — perform the idempotent upsert.
    Rsvp.objects.update_or_create(
        event=event,
        member_id=member_id,
        defaults={"status": status},
    )
    body = (
        f'<h1 class="ok">{escape(_("Thanks — your availability is saved."))}</h1>'
        f"<p>{escape(event_name)} — <strong>{escape(str(status_label))}</strong></p>"
        f'<p class="muted">{escape(_("You can change it any time from the app."))}</p>'
    )
    return _page(_("Availability saved"), body)

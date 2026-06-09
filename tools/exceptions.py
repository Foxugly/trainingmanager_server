from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import APIException, ErrorDetail, PermissionDenied, ValidationError
from rest_framework.views import exception_handler


class NotAManagerDenied(PermissionDenied):
    """403 raised when the caller is neither owner nor manager of the team
    behind a Program / Event. Frontend matches on `code == "not_a_manager"`."""

    default_detail = _("You must be owner or manager of this team.")
    default_code = "not_a_manager"


class NotAuthorizedMemberDenied(PermissionDenied):
    """403 raised when a manager tries to attach a Member to their team that
    they have no legitimate relationship to (the member belongs only to teams
    the caller does not manage and already has an active membership elsewhere).
    Frontend matches on `code == "not_authorized_member"`."""

    default_detail = _("You may only add members you already manage, or brand-new members.")
    default_code = "not_authorized_member"


class NotAuthorizedEventDenied(PermissionDenied):
    """403 raised on event-scoped mutations (e.g. POST rounds-reorder/) when
    the caller does not manage the event's parent program team."""

    default_detail = _("You must manage this event's team to perform this action.")
    default_code = "not_authorized_event"


class NotAuthorizedRoundDenied(PermissionDenied):
    """403 raised on round-scoped mutations (e.g. POST exercises-reorder/)
    when the caller does not manage any event team linked to the round."""

    default_detail = _("You must manage at least one team owning an event linked to this round.")
    default_code = "not_authorized_round"


class ResourceLocked(APIException):
    status_code = 409
    default_detail = _(
        "This resource is locked because it is used in multiple places. Clone it to modify."
    )
    default_code = "resource_locked"


class EmailNotVerified(APIException):
    """JWT login refused because the user's primary email is unverified.

    Frontend matches on `code == "email_not_verified"` to expose the
    'Resend confirmation email' affordance."""

    status_code = 400
    default_detail = _(
        "Email not verified. Please check your inbox or request a new confirmation link."
    )
    default_code = "email_not_verified"


class CaptchaFailed(APIException):
    """Cloudflare Turnstile token was missing, invalid, expired, or the
    siteverify call failed (fail-closed). Frontend should surface a
    "please retry the captcha" message and re-render the widget."""

    status_code = 400
    default_detail = _("Captcha verification failed. Please try again.")
    default_code = "captcha_failed"


class TeamQuotaExceeded(APIException):
    """User attempted to create a team beyond their `team_quota`. Soft-deleted
    teams (is_active=False) free up a slot. The exception payload is enriched
    by the view with `used` / `max` so the frontend can show context."""

    status_code = 403
    default_detail = _(
        "You have reached your team quota. Soft-delete an existing team or "
        "contact an admin to raise your quota."
    )
    default_code = "team_quota_exceeded"


class OwnsTeams(APIException):
    """409 raised when an authenticated user tries to delete their own
    account while still owning one or more teams. Deletion is refused
    (Team.owner is on_delete=PROTECT — even a soft-deleted team blocks it);
    the user must first transfer ownership of those teams. Frontend matches
    on `code == "owns_teams"`."""

    status_code = 409
    default_detail = _("Transfer or delete your teams before deleting your account.")
    default_code = "owns_teams"


class RotiDisabled(PermissionDenied):
    """403 raised when an athlete tries to submit a ROTI on a team whose
    `roti_enabled` toggle is False. Frontend matches on
    `code == "roti_disabled"`."""

    default_detail = _("ROTI is not enabled for this team.")
    default_code = "roti_disabled"


class NotAnAthleteMember(PermissionDenied):
    """403 raised when the caller is not an active athlete-member of the
    event's team and tries to submit a ROTI / RSVP. Frontend matches on
    `code == "not_an_athlete_member"`."""

    default_detail = _("Only athlete-members of the team can submit a ROTI.")
    default_code = "not_an_athlete_member"


class RsvpDisabled(PermissionDenied):
    """403 raised when an athlete tries to submit an RSVP on a team whose
    `rsvp_enabled` toggle is False. Frontend matches on
    `code == "rsvp_disabled"`."""

    default_detail = _("RSVP is not enabled for this team.")
    default_code = "rsvp_disabled"


def custom_exception_handler(exc, context):
    """Normalise every 4xx error to {code, detail, fields?}.

    - APIException subclasses with default_code -> {code, detail}.
    - ValidationError dict (multi-field) -> {code, detail, fields}.
    - ValidationError list/str -> {code, detail}.
    - DRF builtins (NotAuthenticated, PermissionDenied, NotFound,
      MethodNotAllowed, Throttled, AuthenticationFailed, ...) carry
      default_code, so they fall under the APIException branch.
    """
    response = exception_handler(exc, context)
    if response is None:
        return response

    if isinstance(exc, ValidationError):
        response.data = _normalize_validation_error(exc.detail)
        return response

    if isinstance(exc, APIException) and isinstance(response.data, dict):
        if "detail" in response.data and "code" not in response.data:
            code = getattr(exc, "default_code", None)
            if code:
                response.data = {"code": code, "detail": response.data["detail"]}

    return response


def _normalize_validation_error(detail):
    if isinstance(detail, ErrorDetail) or isinstance(detail, str):
        return {"code": "validation_error", "detail": str(detail)}

    if isinstance(detail, list):
        if not detail:
            return {"code": "validation_error", "detail": str(_("Validation failed."))}
        return {"code": "validation_error", "detail": str(detail[0])}

    if isinstance(detail, dict):
        # APIException-like dict: {"detail": "...", "code"?: "..."}
        # raised via raise serializers.ValidationError({"detail": "..."}).
        if set(detail.keys()) <= {"detail", "code"}:
            inner = detail.get("detail")
            inner_str = str(inner[0]) if isinstance(inner, list) and inner else str(inner)
            inner_code = detail.get("code")
            if isinstance(inner, ErrorDetail) and not inner_code:
                inner_code = inner.code
            return {
                "code": str(inner_code) if inner_code else "validation_error",
                "detail": inner_str,
            }

        fields = {name: _normalize_field_errors(errs) for name, errs in detail.items()}
        return {
            "code": "validation_error",
            "detail": str(_("Some fields are invalid.")),
            "fields": fields,
        }

    return {"code": "validation_error", "detail": str(detail)}


def _normalize_field_errors(errors):
    if not isinstance(errors, list):
        errors = [errors]
    out = []
    for err in errors:
        if isinstance(err, ErrorDetail):
            out.append({"code": err.code or "invalid", "detail": str(err)})
        elif isinstance(err, dict):
            out.append(
                {
                    "code": str(err.get("code", "invalid")),
                    "detail": str(err.get("detail", err)),
                }
            )
        else:
            out.append({"code": "invalid", "detail": str(err)})
    return out

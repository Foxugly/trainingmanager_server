import logging

from django.conf import settings as dj_settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone, translation
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers as drf_serializers
from rest_framework import status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Team, TeamJoinRequest, TeamMembership
from ..permissions import IsJoinRequestParticipant
from ..queries import managed_teams
from ..serializers import (
    CreateJoinRequestSerializer,
    JoinMagicCancelledResponseSerializer,
    JoinMagicErrorSerializer,
    TeamJoinRequestMagicActionPostSerializer,
    TeamJoinRequestMagicActionResponseSerializer,
    TeamJoinRequestSerializer,
)

logger = logging.getLogger(__name__)


class TeamJoinRequestViewSet(viewsets.ModelViewSet):
    """Self-signup join request flow."""

    permission_classes = [IsAuthenticated, IsJoinRequestParticipant]
    filterset_fields = ["status", "team"]
    ordering_fields = ["requested_at", "responded_at"]
    ordering = ["-requested_at"]

    def get_serializer_class(self):
        if self.action == "create":
            return CreateJoinRequestSerializer
        return TeamJoinRequestSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return TeamJoinRequest.objects.none()
        user = self.request.user
        return TeamJoinRequest.objects.filter(
            Q(user=user) | Q(team__in=managed_teams(user))
        ).distinct()

    def perform_create(self, serializer):
        instance = serializer.save(user=self.request.user)

        # Auto-accept policy: short-circuit the manual flow.
        if instance.team.join_request_policy == Team.JoinRequestPolicy.AUTO:
            instance.status = "accepted"
            instance.responded_at = timezone.now()
            instance.responded_by = None  # accepted by policy, not by a person
            instance.save(update_fields=["status", "responded_at", "responded_by"])
            self._handle_acceptance(instance)
            return

        # Manual policy + opt-in notification: send email with magic links.
        if instance.team.notify_managers_on_join_request:
            self._notify_managers(instance)

    def _notify_managers(self, join_request):
        """Send the join-request notification to each owner+manager in the
        recipient's own language. Owner is also a manager candidate — dedupe
        on email so they don't get the mail twice if they appear in both."""
        from ..magic_action import magic_link

        # Re-fetch team with owner select_related + managers prefetched so
        # the recipient resolution below does not trigger per-row queries.
        team = (
            Team.objects.select_related("owner")
            .prefetch_related("managers")
            .get(pk=join_request.team_id)
        )
        # Map email -> language for each recipient. Owner takes priority over
        # managers' duplicate entries.
        recipients_by_email: dict[str, str] = {}
        for mgr in team.managers.all():
            if mgr.email:
                recipients_by_email[mgr.email] = mgr.language or "en"
        if team.owner.email:
            recipients_by_email[team.owner.email] = team.owner.language or "en"

        if not recipients_by_email:
            return
        accept_url = magic_link(join_request.id, "accept")
        reject_url = magic_link(join_request.id, "reject")

        for email, lang in recipients_by_email.items():
            with translation.override(lang):
                # gettext (not lazy) here so the strings are resolved INSIDE
                # the override block; gettext_lazy would defer resolution to
                # the moment the string is used, which on f-string formatting
                # happens immediately and would be fine, but explicit eager
                # resolution makes the intent unambiguous.
                subject = f"[TrainingManager] {_('Join request from')} {join_request.user.username}"
                body = (
                    f"{join_request.user.username} ({join_request.user.email}) "
                    f'{_("wants to join your team")} "{team.name}".\n\n'
                    f"{_('Message')}: {join_request.message or _('(none)')}\n\n"
                    f"{_('Accept')}: {accept_url}\n"
                    f"{_('Reject')}: {reject_url}\n\n"
                    f"{_('Links are valid for 48 hours. You can also respond from the team dashboard.')}"
                )
                try:
                    send_mail(
                        subject=str(subject),
                        message=str(body),
                        from_email=dj_settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[email],
                        fail_silently=False,
                    )
                except Exception:
                    logger.exception("Failed to send join-request notification to %s", email)

    def perform_update(self, serializer):
        instance = serializer.instance
        new_status = serializer.validated_data.get("status", instance.status)

        if new_status == instance.status:
            serializer.save()
            return

        if instance.status != "pending":
            raise drf_serializers.ValidationError(
                {"status": _("This request has already been handled.")},
                code="request_already_handled",
            )

        if new_status == "cancelled":
            if instance.user_id != self.request.user.pk:
                raise drf_serializers.ValidationError(
                    {"status": _("Only the requester can cancel this request.")},
                    code="only_owner_can_cancel",
                )
            serializer.save(responded_at=timezone.now())
            return

        if new_status in ("accepted", "rejected"):
            if not instance.team.is_managed_by(self.request.user):
                raise drf_serializers.ValidationError(
                    {"status": _("Only a manager can accept or reject this request.")},
                    code="only_manager_can_respond",
                )
            saved = serializer.save(
                responded_at=timezone.now(),
                responded_by=self.request.user,
            )
            if new_status == "accepted":
                self._handle_acceptance(saved)
            return

        raise drf_serializers.ValidationError(
            {"status": _("Unauthorized status transition.")},
            code="invalid_status_transition",
        )

    @staticmethod
    def _handle_acceptance(join_request):
        from django.db import IntegrityError

        from member.models import Member

        user = join_request.user
        team = join_request.team

        existing_member = getattr(user, "member_profile", None)
        if existing_member is not None:
            if not TeamMembership.objects.filter(
                team=team, member=existing_member, left_at__isnull=True
            ).exists():
                # The .exists() check is the fast path; the partial unique
                # constraint (uniq_active_membership_per_team_member) is the
                # real guard against a concurrent accept racing between the
                # check and the insert — swallow its IntegrityError as a no-op.
                try:
                    with transaction.atomic():
                        TeamMembership.objects.create(team=team, member=existing_member)
                except IntegrityError:
                    pass
            return

        member = Member.objects.create(
            firstname=user.first_name or user.username,
            lastname=user.last_name or "",
            email=user.email,
            phonenumber="",
            user=user,
        )
        try:
            with transaction.atomic():
                TeamMembership.objects.create(team=team, member=member)
        except IntegrityError:
            pass

    @staticmethod
    def _revoke_membership(join_request):
        """Reverse a previous acceptance — used when a manager flips the
        decision via magic link from accepted -> rejected. Sets left_at on
        the active TeamMembership for (team, requester); does not delete.

        Saves each row individually (NOT queryset.update) so the
        post_save signal sync_event_members_on_membership_change fires and
        DETACHES the athlete from the team's future events — a bulk .update()
        bypasses signals and would leave them attached."""
        member = getattr(join_request.user, "member_profile", None)
        if member is None:
            return
        now = timezone.now()
        for membership in TeamMembership.objects.filter(
            team=join_request.team,
            member=member,
            left_at__isnull=True,
        ):
            membership.left_at = now
            membership.save(update_fields=["left_at", "updated_at"])


class _MagicActionBase(APIView):
    """Shared behaviour for the magic-action preview (GET) and execute (POST)
    endpoints. Split into two concrete views so each only exposes the
    relevant HTTP method (avoids drf-spectacular operationId collisions)."""

    permission_classes = [IsAuthenticated]

    def _resolve(self, token):
        from ..magic_action import parse_token

        parsed = parse_token(token)
        if parsed is None:
            raise drf_serializers.ValidationError(
                {"detail": _("Invalid or expired magic-action token.")},
                code="invalid_or_expired_token",
            )
        jr_id, action = parsed
        join_request = get_object_or_404(TeamJoinRequest, pk=jr_id)
        if not join_request.team.is_managed_by(self.request.user):
            raise PermissionDenied(_("You are not a manager of this team."))
        return join_request, action

    def _serialize(self, join_request, action_proposed, previous_status=None):
        """Build the magic-action response payload.

        If `previous_status` is given (POST execute path), `would_change_decision`
        reflects whether the executed action reversed a previous decision —
        otherwise (GET preview path) it predicts whether the proposed action
        WOULD reverse the current state."""
        responded_by = join_request.responded_by.username if join_request.responded_by else None
        target_status = "accepted" if action_proposed == "accept" else "rejected"
        if previous_status is None:
            # GET preview: compare against current state.
            would_change = (join_request.status == "accepted" and action_proposed == "reject") or (
                join_request.status == "rejected" and action_proposed == "accept"
            )
        else:
            # POST execute: compare the new state against what it was before.
            would_change = previous_status in ("accepted", "rejected") and (
                previous_status != target_status
            )
        return {
            "join_request": {
                "id": join_request.id,
                "team_id": join_request.team_id,
                "team_name": join_request.team.name,
                "requester_username": join_request.user.username,
                "requester_email": join_request.user.email,
                "message": join_request.message,
                "requested_at": join_request.requested_at.isoformat(),
                "status": join_request.status,
                "responded_at": (
                    join_request.responded_at.isoformat() if join_request.responded_at else None
                ),
                "responded_by": responded_by,
            },
            "action_proposed": action_proposed,
            "would_change_decision": would_change,
            "can_act": join_request.status != "cancelled",
        }


class TeamJoinRequestMagicActionPreviewView(_MagicActionBase):
    """GET /api/v1/join-magic/<token>/ — preview only. No state change.

    Safe for email link previewers (Outlook safe-links, Gmail bots).
    Returns the join request + action proposed by the token + current
    status + whether the action would reverse a previous decision +
    whether the action can still be performed (false if cancelled)."""

    @extend_schema(
        responses={
            200: TeamJoinRequestMagicActionResponseSerializer,
            400: OpenApiResponse(
                response=JoinMagicErrorSerializer,
                description="Invalid or expired token (code=invalid_or_expired_token).",
            ),
            403: OpenApiResponse(description="Not a manager of this team."),
            404: OpenApiResponse(description="Join request not found."),
        }
    )
    def get(self, request, token):
        join_request, action = self._resolve(token)
        return Response(self._serialize(join_request, action))


class TeamJoinRequestMagicActionExecuteView(_MagicActionBase):
    """POST /api/v1/join-magic/ {token} — executes the encoded action.

    Reversal support (E-b semantic):
      - pending  + accept -> accepted (creates TeamMembership)
      - pending  + reject -> rejected (no membership)
      - accepted + reject -> rejected (revokes membership: sets left_at)
      - rejected + accept -> accepted (creates membership again)
      - cancelled + *    -> 409 conflict (requester withdrew, irreversible)
    responded_by is updated to the manager who made the latest call.
    Idempotent when the request is already in the target status."""

    @extend_schema(
        request=TeamJoinRequestMagicActionPostSerializer,
        responses={
            200: OpenApiResponse(
                response=TeamJoinRequestMagicActionResponseSerializer,
                description=(
                    "Action executed (or no-op if already in target status). "
                    "Returns the same payload shape as the preview, with "
                    "the join_request reflecting the new status."
                ),
            ),
            400: OpenApiResponse(
                response=JoinMagicErrorSerializer,
                description=(
                    "Invalid or expired token (code=invalid_or_expired_token), "
                    "or missing token in body (code=token_required)."
                ),
            ),
            403: OpenApiResponse(description="Not a manager of this team."),
            404: OpenApiResponse(description="Join request not found."),
            409: OpenApiResponse(
                response=JoinMagicCancelledResponseSerializer,
                description=(
                    "Conflict: the request was cancelled by the requester and "
                    "cannot be revived (code=request_cancelled). The body still "
                    "contains the regular response payload alongside code/detail "
                    "so the frontend can show context."
                ),
            ),
        },
    )
    def post(self, request):
        token = request.data.get("token")
        if not token:
            # Pass the code via the {detail, code} dict shape so
            # custom_exception_handler surfaces it at the top level
            # ({"code": "token_required", "detail": "..."}). The {"token":
            # ...} shape would have buried the code under fields.token[0].
            raise drf_serializers.ValidationError(
                {"detail": _("token is required.")}, code="token_required"
            )
        join_request, action = self._resolve(token)
        target_status = "accepted" if action == "accept" else "rejected"

        if join_request.status == "cancelled":
            return Response(
                {
                    "code": "request_cancelled",
                    "detail": _(
                        "This join request was cancelled by the requester and cannot be acted on."
                    ),
                    **self._serialize(join_request, action),
                },
                status=status.HTTP_409_CONFLICT,
            )

        if join_request.status == target_status:
            # Idempotent: nothing to do, but report the current state so the
            # frontend can show "already X by Y on Z".
            return Response(self._serialize(join_request, action))

        previous_status = join_request.status

        with transaction.atomic():
            join_request.status = target_status
            join_request.responded_at = timezone.now()
            join_request.responded_by = request.user
            join_request.save(update_fields=["status", "responded_at", "responded_by"])
            if target_status == "accepted":
                TeamJoinRequestViewSet._handle_acceptance(join_request)
            elif previous_status == "accepted":
                TeamJoinRequestViewSet._revoke_membership(join_request)

        return Response(self._serialize(join_request, action, previous_status=previous_status))

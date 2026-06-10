import base64
import binascii
import logging
import re

from django.conf import settings as dj_settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone, translation
from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import serializers as drf_serializers
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tools.exceptions import NotAuthorizedMemberDenied, TeamQuotaExceeded
from tools.mixins import TeamScopedViewMixin
from tools.openapi import INCLUDE_INACTIVE_PARAM
from tools.throttling import AIReviewThrottle

from .models import Team, TeamInvitation, TeamJoinRequest, TeamMembership, TrainingSlot
from .permissions import (
    IsJoinRequestParticipant,
    IsTeamManagerOrReadOnly,
    IsTrainer,
)
from .queries import managed_teams, user_member_teams, user_visible_teams
from .serializers import (
    CompleteInvitationSerializer,
    CreateInvitationSerializer,
    CreateJoinRequestSerializer,
    JoinMagicCancelledResponseSerializer,
    JoinMagicErrorSerializer,
    ReviewBlockRequestSerializer,
    ReviewBlockResponseSerializer,
    RosterHistoryResponseSerializer,
    RotiDriftResponseSerializer,
    RsvpReliabilityResponseSerializer,
    TeamInvitationSerializer,
    TeamJoinRequestMagicActionPostSerializer,
    TeamJoinRequestMagicActionResponseSerializer,
    TeamJoinRequestSerializer,
    TeamMembershipSerializer,
    TeamSerializer,
    TeamStatsSerializer,
    TrainingSlotSerializer,
    TrainingTemplateSerializer,
    ValidateInvitationSerializer,
)
from .stats import assemble_stats, parse_window, roti_drift, rsvp_reliability

logger = logging.getLogger(__name__)


class TeamPoolsResponseSerializer(drf_serializers.Serializer):
    """Response of GET /teams/{id}/pools/.

    The distinct, non-empty session locations ("piscines") used across the
    team's events, for the frontend's location autocomplete.
    """

    pools = drf_serializers.ListField(child=drf_serializers.CharField())


class TeamViewSet(viewsets.ModelViewSet):
    """CRUD sur Teams. Liste = teams gérées par l'user + teams publiques actives."""

    serializer_class = TeamSerializer
    permission_classes = [IsAuthenticated, IsTeamManagerOrReadOnly]
    filterset_fields = ["is_active", "is_public", "language"]
    search_fields = ["name"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Team.objects.none()
        return (
            user_visible_teams(self.request.user)
            .select_related("owner", "default_place")
            .prefetch_related(
                "managers",
                "places",
                "equipment",
                "attendance_statuses",
                "team_sports__sport",
            )
        )

    def perform_create(self, serializer):
        user = self.request.user
        used = user.active_owned_teams_count()
        if used >= user.team_quota:
            # Enrich the exception with quota context so the response body
            # includes used/max/can_create alongside code+detail.
            exc = TeamQuotaExceeded()
            exc.detail = {
                "code": exc.default_code,
                "detail": str(exc.default_detail),
                "used": used,
                "max": user.team_quota,
                "can_create": False,
            }
            raise exc
        serializer.save(owner=user)

    @extend_schema(
        summary="Team logo image (public)",
        description=(
            "Returns the team's logo as a binary image, decoded from the stored "
            "base64 data-URL. Public (no auth) so it can be used directly as an "
            "<img> src; returns 404 when the team has no logo. The team list and "
            "detail expose this URL as `logo_url` instead of inlining the base64."
        ),
        responses={200: OpenApiTypes.BINARY, 404: OpenApiResponse(description="No logo")},
    )
    @action(detail=True, methods=["get"], permission_classes=[AllowAny], url_path="logo")
    def logo(self, request, pk=None):
        # Public on purpose (product decision): logos are branding images and an
        # <img src> cannot carry the JWT. Fetched directly, bypassing the
        # member-scoped get_queryset.
        team = get_object_or_404(Team, pk=pk)
        match = re.match(r"^data:(image/[\w.+-]+);base64,(.*)$", team.logo or "", re.DOTALL)
        if not match:
            raise Http404("This team has no logo.")
        mime, b64 = match.group(1), match.group(2)
        try:
            raw = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise Http404("Malformed logo.") from exc
        response = HttpResponse(raw, content_type=mime)
        response["Cache-Control"] = "public, max-age=300"
        return response

    @extend_schema(
        operation_id="teams_stats_retrieve",
        summary="Team statistics (attendance, volume, intensity)",
        description=(
            "Read-only aggregated statistics for the team's events whose "
            "`date` falls in the window [`from`, `to`] (both inclusive). "
            "Defaults to the last 12 weeks (`to` = today, `from` = today - 84 "
            "days) when both are absent; a single bound fills the other "
            "(`to` defaults to today, `from` defaults to `to` - 84 days). The "
            "span is clamped to a maximum of 2 years.\n\n"
            "Without `member` the payload is the **team aggregate** "
            "(owner/manager only). With `member=<id>` the payload is **scoped "
            "to that athlete**: allowed for the team's owner/managers for any "
            "member, or for the athlete viewing their OWN member record."
        ),
        parameters=[
            OpenApiParameter(
                name="from",
                type=OpenApiTypes.DATE,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Window start (inclusive, ISO YYYY-MM-DD). Defaults to "
                    "`to` - 84 days."
                ),
            ),
            OpenApiParameter(
                name="to",
                type=OpenApiTypes.DATE,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Window end (inclusive, ISO YYYY-MM-DD). Defaults to today."
                ),
            ),
            OpenApiParameter(
                name="member",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Optional member id. When set, scopes the whole payload to "
                    "that athlete. Owner/managers may request any member of the "
                    "team; an athlete may only request their own member id "
                    "(otherwise 403). Member not in the team -> 404."
                ),
            ),
        ],
        responses={200: TeamStatsSerializer},
    )
    @action(detail=True, methods=["get"], url_path="stats")
    def stats(self, request, pk=None):
        """GET /teams/{id}/stats/ — read-only aggregated stats.

        Object-level perms only gate writes (SAFE_METHODS pass through), so
        we enforce access explicitly here:
          - no ?member=     -> team aggregate, owner/manager only.
          - ?member=<id>    -> scoped to that athlete; owner/manager (any
                               member) or the athlete themselves (own id only).
        """
        team = self.get_object()
        is_manager = team.is_managed_by(request.user)

        # Resolve the optional per-athlete scope + enforce permissions.
        scope_member = self._resolve_member_scope(request, team, is_manager)

        date_from, date_to = parse_window(request)
        payload = assemble_stats(team, date_from, date_to, scope_member)
        return Response(TeamStatsSerializer(payload).data)

    @extend_schema(
        operation_id="teams_review_block_create",
        summary="AI review / critique of the team's training block",
        description=(
            "Managers only. Runs an AI analysis over the team's recorded "
            "training data for the [from, to] window (same window params as "
            "/stats/) and returns a structured critique: overall load "
            "assessment, findings, and recommended adjustments. Throttled to "
            "10/hour per user."
        ),
        parameters=[
            OpenApiParameter("from", OpenApiTypes.DATE, description="Window start (ISO)."),
            OpenApiParameter("to", OpenApiTypes.DATE, description="Window end (ISO)."),
        ],
        request=ReviewBlockRequestSerializer,
        responses={
            200: ReviewBlockResponseSerializer,
            400: OpenApiResponse(description="Invalid window"),
            403: OpenApiResponse(description="Not a team manager"),
            500: OpenApiResponse(description="AI configuration error"),
            502: OpenApiResponse(description="AI service error"),
        },
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="review-block",
        throttle_classes=[AIReviewThrottle],
    )
    def review_block(self, request, pk=None):
        """POST /teams/{id}/review-block/ — AI critique of the team's block."""
        from .ai_review import review_training_block

        team = self.get_object()
        if not team.is_managed_by(request.user):
            raise PermissionDenied(
                _("Only the team owner or managers can request a training review.")
            )

        body = ReviewBlockRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        date_from, date_to = parse_window(request)

        # Run the analysis in the team's language so translatable labels in the
        # assembled stats (energy-zone descriptions) match the prose.
        with translation.override(team.language or "en"):
            stats_payload = assemble_stats(team, date_from, date_to, scope_member=None)
            ai_result = review_training_block(
                team=team,
                date_from=date_from,
                date_to=date_to,
                stats=stats_payload,
                user=request.user if request.user.is_authenticated else None,
            )

        payload = {
            "period": {"from": date_from, "to": date_to},
            "summary": ai_result["summary"],
            "load_assessment": ai_result["load_assessment"],
            "findings": ai_result["findings"],
            "adjustments": ai_result["adjustments"],
            "confidence": ai_result["confidence"],
            "model": ai_result["model"],
            "tokens_used": {
                "input": ai_result["input_tokens"],
                "output": ai_result["output_tokens"],
            },
        }
        return Response(ReviewBlockResponseSerializer(payload).data)

    @extend_schema(
        operation_id="teams_pools_retrieve",
        summary="Distinct session locations (pools) for autocomplete",
        description=(
            "Read-only list of the distinct, non-empty `location` values used "
            "across this team's events, ordered alphabetically. Intended for "
            "the session-editor location ('piscine') autocomplete. Any member "
            "of the team (owner, manager, or active athlete) may read it; "
            "non-members get 404 (the team is not in their visible scope)."
        ),
        responses={200: TeamPoolsResponseSerializer},
    )
    @action(detail=True, methods=["get"], url_path="pools")
    def pools(self, request, pk=None):
        """GET /teams/{id}/pools/ — distinct non-empty event locations.

        Permission: the requester must be a strict member of the team
        (owner/manager/active athlete). The base get_queryset scopes to
        user_visible_teams (which includes discoverable public teams), so we
        re-check strict membership here via user_member_teams and 404 a
        non-member — a public-team discoverer must not read its content.
        """
        from rest_framework.exceptions import NotFound

        from event.models import Event

        team = self.get_object()
        if not user_member_teams(request.user).filter(pk=team.pk).exists():
            raise NotFound()

        pools = list(
            Event.objects.filter(refer_program__team=team)
            .exclude(location="")
            .exclude(location__isnull=True)
            .values_list("location", flat=True)
            .distinct()
        )
        pools = sorted(set(pools), key=lambda s: s.lower())
        return Response(TeamPoolsResponseSerializer({"pools": pools}).data)

    @staticmethod
    def _template_payload(team):
        """Assemble the current training-template payload for a team."""
        slots = [
            {
                "weekday": s.weekday,
                "hour_start": s.hour_start,
                "hour_end": s.hour_end,
                "place": s.place,
            }
            for s in team.training_slots.select_related("place").all()
        ]
        return {
            "slots": slots,
            "default_pool": team.default_pool,
            "season_start": team.season_start,
            "season_end": team.season_end,
        }

    @extend_schema(
        methods=["get"],
        operation_id="teams_training_template_retrieve",
        summary="Read the team's weekly training template",
        description=(
            "Returns the team's reusable weekly training template: the list of "
            "weekly slots (weekday Monday=0…Sunday=6 + hour_start/hour_end), the "
            "default pool/venue, and the default season dates. Any member of the "
            "team (owner, manager, or active athlete) may read it; non-members "
            "get 404. Read-only: slots are written via the per-slot CRUD under "
            "`teams/{id}/training-slots/`."
        ),
        responses={200: TrainingTemplateSerializer},
    )
    @action(detail=True, methods=["get"], url_path="training-template")
    def training_template(self, request, pk=None):
        """GET /teams/{id}/training-template/ — the weekly template (read-only).

        Writes go through the per-slot TrainingSlot CRUD; this endpoint only
        aggregates the template for the AI plan generator's prefill.
        """
        from rest_framework.exceptions import NotFound

        team = self.get_object()
        # Any strict member may read; non-member -> 404 (mirrors pools).
        if not user_member_teams(request.user).filter(pk=team.pk).exists():
            raise NotFound()
        return Response(TrainingTemplateSerializer(self._template_payload(team)).data)

    @extend_schema(
        operation_id="teams_roster_history_retrieve",
        summary="Team roster history (membership periods)",
        description=(
            "Manager-only. Every membership row — active AND past — with the "
            "member's name and joined_at/left_at, for season-review / churn "
            "analysis (TeamMembership keeps full join/leave history). Ordered by "
            "member name then join date."
        ),
        responses={200: RosterHistoryResponseSerializer},
    )
    @action(detail=True, methods=["get"], url_path="roster-history")
    def roster_history(self, request, pk=None):
        """GET /teams/{id}/roster-history/ — membership periods (managers only)."""
        team = self.get_object()
        if not team.is_managed_by(request.user):
            raise PermissionDenied(
                _("Only the team owner or managers can view the roster history.")
            )
        rows = team.memberships.select_related("member").order_by(
            "member__lastname", "member__firstname", "joined_at"
        )
        entries = [
            {
                "member_id": m.member_id,
                "name": f"{m.member.firstname} {m.member.lastname}".strip(),
                "joined_at": m.joined_at,
                "left_at": m.left_at,
                "active": m.left_at is None,
            }
            for m in rows
        ]
        return Response(RosterHistoryResponseSerializer({"entries": entries}).data)

    @extend_schema(
        operation_id="teams_rsvp_reliability_retrieve",
        summary="Per-athlete RSVP reliability (going-but-absent)",
        description=(
            "Manager-only. Over the [from, to] window (same params as /stats/), "
            "for each athlete who RSVP'd GOING: how many of those sessions they "
            "were actually present at (shows), missed (no_shows), and the "
            "reliability rate (shows/going). Sorted worst-reliability first. "
            "Athletes with no GOING RSVP in the window are omitted."
        ),
        parameters=[
            OpenApiParameter("from", OpenApiTypes.DATE, description="Window start (ISO)."),
            OpenApiParameter("to", OpenApiTypes.DATE, description="Window end (ISO)."),
        ],
        responses={200: RsvpReliabilityResponseSerializer},
    )
    @action(detail=True, methods=["get"], url_path="rsvp-reliability")
    def rsvp_reliability(self, request, pk=None):
        """GET /teams/{id}/rsvp-reliability/ — going-but-absent per athlete."""
        team = self.get_object()
        if not team.is_managed_by(request.user):
            raise PermissionDenied(
                _("Only the team owner or managers can view RSVP reliability.")
            )
        date_from, date_to = parse_window(request)
        entries = rsvp_reliability(team, date_from, date_to)
        return Response(
            RsvpReliabilityResponseSerializer(
                {"period": {"from": date_from, "to": date_to}, "entries": entries}
            ).data
        )

    @extend_schema(
        operation_id="teams_roti_drift_retrieve",
        summary="Per-athlete ROTI drift vs the squad",
        description=(
            "Manager-only. Over the [from, to] window (same params as /stats/), "
            "compares each athlete's mean ROTI (perceived session difficulty, "
            "1..5) to the squad mean. flag=high (>= +0.75) flags possible "
            "overreaching; flag=low (<= -0.75) possible under-challenge. Sorted "
            "by |delta| desc (most divergent first)."
        ),
        parameters=[
            OpenApiParameter("from", OpenApiTypes.DATE, description="Window start (ISO)."),
            OpenApiParameter("to", OpenApiTypes.DATE, description="Window end (ISO)."),
        ],
        responses={200: RotiDriftResponseSerializer},
    )
    @action(detail=True, methods=["get"], url_path="roti-drift")
    def roti_drift(self, request, pk=None):
        """GET /teams/{id}/roti-drift/ — perceived-effort drift per athlete."""
        team = self.get_object()
        if not team.is_managed_by(request.user):
            raise PermissionDenied(
                _("Only the team owner or managers can view ROTI drift.")
            )
        date_from, date_to = parse_window(request)
        result = roti_drift(team, date_from, date_to)
        return Response(
            RotiDriftResponseSerializer(
                {"period": {"from": date_from, "to": date_to}, **result}
            ).data
        )

    def _resolve_member_scope(self, request, team, is_manager):
        """Resolve the optional ?member=<id> scope and enforce permissions.

        Returns ``None`` for the team aggregate (no ?member=), or a
        ``{"id", "name"}`` dict for a valid, authorized per-athlete scope.

        Permission matrix:
          - no member: aggregate -> owner/manager only (raise 403 otherwise).
          - member set: must be an active member of the team (else 404). A
            manager may request any such member; a non-manager may only
            request their OWN member record (member.user == request.user),
            else 403.
        """
        raw = request.query_params.get("member")

        if raw is None or raw == "":
            # Team aggregate: owner/manager only.
            if not is_manager:
                raise PermissionDenied(
                    _("Only the team owner or managers can view team statistics.")
                )
            return None

        try:
            member_id = int(raw)
        except (TypeError, ValueError):
            raise drf_serializers.ValidationError(
                {"member": _("member must be an integer id.")},
                code="invalid_member",
            )

        membership = (
            team.memberships.filter(member_id=member_id, left_at__isnull=True)
            .select_related("member")
            .first()
        )
        if membership is None:
            from rest_framework.exceptions import NotFound

            raise NotFound(_("No such member in this team."))

        member = membership.member
        if not is_manager:
            # A non-manager may only view their own stats.
            if member.user_id != request.user.pk:
                raise PermissionDenied(
                    _("You can only view your own statistics.")
                )

        name = f"{member.firstname} {member.lastname}".strip()
        return {"id": member_id, "name": name}


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
        from .magic_action import magic_link

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
        from member.models import Member

        user = join_request.user
        team = join_request.team

        existing_member = getattr(user, "member_profile", None)
        if existing_member is not None:
            if not TeamMembership.objects.filter(
                team=team, member=existing_member, left_at__isnull=True
            ).exists():
                TeamMembership.objects.create(team=team, member=existing_member)
            return

        member = Member.objects.create(
            firstname=user.first_name or user.username,
            lastname=user.last_name or "",
            email=user.email,
            phonenumber="",
            user=user,
        )
        TeamMembership.objects.create(team=team, member=member)

    @staticmethod
    def _revoke_membership(join_request):
        """Reverse a previous acceptance — used when a manager flips the
        decision via magic link from accepted -> rejected. Sets left_at on
        the active TeamMembership for (team, requester); does not delete."""
        member = getattr(join_request.user, "member_profile", None)
        if member is None:
            return
        TeamMembership.objects.filter(
            team=join_request.team,
            member=member,
            left_at__isnull=True,
        ).update(left_at=timezone.now())


class _MagicActionBase(APIView):
    """Shared behaviour for the magic-action preview (GET) and execute (POST)
    endpoints. Split into two concrete views so each only exposes the
    relevant HTTP method (avoids drf-spectacular operationId collisions)."""

    permission_classes = [IsAuthenticated]

    def _resolve(self, token):
        from .magic_action import parse_token

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


class TeamInvitationViewSet(viewsets.ModelViewSet):
    """Trainer invitation flow."""

    permission_classes = [IsAuthenticated, IsTrainer]
    filterset_fields = ["status", "team"]
    ordering_fields = ["created_at", "expires_at"]
    ordering = ["-created_at"]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_serializer_class(self):
        if self.action == "create":
            return CreateInvitationSerializer
        return TeamInvitationSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return TeamInvitation.objects.none()
        user = self.request.user
        return TeamInvitation.objects.filter(team__in=managed_teams(user)).distinct()

    @extend_schema(
        request=CreateInvitationSerializer,
        responses={
            201: TeamInvitationSerializer,
            400: OpenApiResponse(
                description=(
                    "Validation error. Possible codes include: "
                    "`user_already_registered` when the email matches an existing "
                    "user account (direct enrolment is refused for existing users); "
                    "`email_already_invited`, `not_a_manager`, `team_not_active`."
                ),
            ),
        },
        description=(
            "Trainer pre-registers an athlete on a managed team by sending an "
            "invitation email. Refused with code=user_already_registered if the "
            "email matches an existing user account; the trainer must use a "
            "different flow (e.g. ask the user to issue a TeamJoinRequest) for "
            "registered users."
        ),
    )
    def create(self, request, *args, **kwargs):
        from member.models import Member

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        User = get_user_model()
        if User.objects.filter(email=data["email"]).exists():
            raise drf_serializers.ValidationError(
                {
                    "detail": _(
                        "Cet utilisateur est déjà enregistré. Une invitation "
                        "directe n'est pas possible pour les comptes existants."
                    ),
                },
                code="user_already_registered",
            )

        member = Member.objects.create(
            firstname=data["firstname"],
            lastname=data["lastname"],
            email=data["email"],
            phonenumber=data.get("phonenumber", ""),
        )
        invitation = TeamInvitation.objects.create(
            team=data["team"],
            invited_by=request.user,
            member=member,
            email=data["email"],
        )
        self._send_invitation_email(invitation)
        return Response(
            TeamInvitationSerializer(invitation, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    def _send_invitation_email(self, invitation):
        """The invitee has no user account yet, so we don't know their
        language. Fall back to the team's language, which is the most
        likely match (the invitee is about to join that team)."""
        frontend_url = dj_settings.FRONTEND_URL.rstrip("/")
        link = f"{frontend_url}/invitation/{invitation.token}"
        with translation.override(invitation.team.language or "en"):
            subject = f"[TrainingManager] {_('You are invited to')} {invitation.team.name}"
            body = (
                f"{_('Hello')} {invitation.member.firstname},\n\n"
                f"{invitation.invited_by.username} {_('has invited you to join the team')} "
                f'"{invitation.team.name}".\n\n'
                f"{_('To finalize your registration, click the link below:')}\n"
                f"{link}\n\n"
                f"{_('The link is valid until')} {invitation.expires_at.strftime('%d/%m/%Y')}.\n"
            )
            try:
                send_mail(
                    subject=str(subject),
                    message=str(body),
                    from_email=dj_settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[invitation.email],
                    fail_silently=False,
                )
            except Exception:
                logger.exception("Failed to send invitation email")

    def perform_destroy(self, instance):
        if instance.status != "pending":
            raise drf_serializers.ValidationError(
                {"detail": _("Only pending invitations can be cancelled.")},
                code="invitation_pending_required",
            )
        instance.status = "cancelled"
        instance.save()


class InvitationLookupView(APIView):
    """Public endpoint to validate and finalize an invitation token."""

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        responses={
            200: ValidateInvitationSerializer,
            400: OpenApiResponse(description="Invitation not pending (already handled)"),
            404: OpenApiResponse(description="Token not found"),
            410: OpenApiResponse(description="Invitation expired"),
        },
        description="Lookup an invitation by token. No authentication required.",
    )
    def get(self, request, token):
        invitation = get_object_or_404(TeamInvitation, token=token)
        if not invitation.is_valid():
            if invitation.status == "pending" and timezone.now() > invitation.expires_at:
                invitation.status = "expired"
                invitation.save()
                return Response(
                    {"code": "invitation_expired", "detail": _("Invitation expired.")},
                    status=status.HTTP_410_GONE,
                )
            return Response(
                {
                    "code": f"invitation_{invitation.status}",
                    "detail": _("Invitation is not pending."),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(ValidateInvitationSerializer(invitation).data)

    @extend_schema(
        request=CompleteInvitationSerializer,
        responses={
            201: OpenApiResponse(
                response=inline_serializer(
                    name="CompleteInvitationResponse",
                    fields={
                        "detail": drf_serializers.CharField(),
                        "username": drf_serializers.CharField(),
                        "access": drf_serializers.CharField(),
                        "refresh": drf_serializers.CharField(),
                    },
                ),
                description="User created and JWT issued",
            ),
            400: OpenApiResponse(
                description="Invalid token state, username taken, or weak password"
            ),
            404: OpenApiResponse(description="Token not found"),
        },
        description="Finalize invitation: create the user, link Member, return JWT.",
    )
    def post(self, request, token):
        from allauth.account.models import EmailAddress
        from rest_framework_simplejwt.tokens import RefreshToken

        invitation = get_object_or_404(TeamInvitation, token=token)
        if not invitation.is_valid():
            return Response(
                {
                    "code": f"invitation_{invitation.status}",
                    "detail": _("Invitation is not pending."),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = CompleteInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        User = get_user_model()
        with transaction.atomic():
            user = User.objects.create_user(
                username=serializer.validated_data["username"],
                email=invitation.email,
                password=serializer.validated_data["password"],
                first_name=invitation.member.firstname,
                last_name=invitation.member.lastname,
                is_active=True,
            )
            EmailAddress.objects.create(
                user=user,
                email=invitation.email,
                verified=True,
                primary=True,
            )
            invitation.member.user = user
            invitation.member.save()

            invitation.status = "completed"
            invitation.completed_at = timezone.now()
            invitation.save()

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "detail": _("Account created and invitation finalized."),
                "username": user.username,
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(parameters=[INCLUDE_INACTIVE_PARAM])
class TeamMembershipViewSet(TeamScopedViewMixin, viewsets.ModelViewSet):
    """Manage team memberships.

    URL: /api/v1/teams/{team_pk}/memberships/

    - GET (list): active memberships of the team. Pass ?include_inactive=true
      to also see historical (left_at IS NOT NULL) rows.
    - POST: add a member to the team (manager-only). Idempotent: rejects with
      400 already_member if an active membership already exists for the same
      (team, member) pair.
    - DELETE /{id}/: end a membership (sets left_at = now). Allowed for the
      member herself or a team manager. The team owner cannot leave their own
      team via this endpoint.
    """

    serializer_class = TeamMembershipSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None
    # Membership has no client-editable fields; join/leave is POST/DELETE.
    # Block PUT/PATCH so a member cannot repoint a row to an arbitrary Member.
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return TeamMembership.objects.none()

        team = self.get_team()
        if team is None:
            return TeamMembership.objects.none()

        user = self.request.user
        is_team_member = (
            team.is_managed_by(user)
            or team.memberships.filter(member__user_id=user.pk, left_at__isnull=True).exists()
        )
        if not is_team_member:
            return TeamMembership.objects.none()

        qs = TeamMembership.objects.filter(team=team).select_related("member", "member__user")

        if self.action == "list":
            include_inactive = self.request.query_params.get("include_inactive") == "true"
            if not include_inactive:
                qs = qs.filter(left_at__isnull=True)
        return qs

    def perform_create(self, serializer):
        team = self.get_team()
        if team is None or not team.is_managed_by(self.request.user):
            raise PermissionDenied(_("Only owner and managers can add members to a team."))

        member = serializer.validated_data["member"]
        if TeamMembership.objects.filter(team=team, member=member, left_at__isnull=True).exists():
            raise drf_serializers.ValidationError(
                {"member": _("This member is already in the team.")},
                code="already_member",
            )

        # Authorize the SOURCE of the member, not just the target team: the
        # caller may only pull in a member they already legitimately manage
        # (the member belongs to >=1 team in managed_teams), OR a brand-new
        # member with no active membership anywhere (the invitation/join case).
        # Without this, a manager could attach a stranger's Member (PII) by id.
        manages_member = TeamMembership.objects.filter(
            member=member,
            left_at__isnull=True,
            team__in=managed_teams(self.request.user),
        ).exists()
        has_any_active_membership = TeamMembership.objects.filter(
            member=member, left_at__isnull=True
        ).exists()
        if not (manages_member or not has_any_active_membership):
            raise NotAuthorizedMemberDenied()

        serializer.save(team=team)

    def perform_destroy(self, instance):
        user = self.request.user
        team = instance.team
        is_self = instance.member.user_id == user.pk
        is_team_manager = team.is_managed_by(user)

        if not (is_self or is_team_manager):
            raise PermissionDenied(_("You can only remove yourself or you must be a team manager."))
        if is_self and team.owner_id == user.pk:
            raise PermissionDenied(
                _(
                    "Team owner cannot leave their own team. "
                    "Transfer ownership first or delete the team."
                )
            )
        if instance.left_at is None:
            instance.left_at = timezone.now()
            instance.save(update_fields=["left_at", "updated_at"])

            # Audit the membership end (best-effort; never breaks the action).
            from audit.models import AuditAction
            from audit.services import record

            member = instance.member
            record(
                AuditAction.MEMBER_REMOVED,
                actor=user,
                team=team,
                target_repr=f"Member #{member.id} ({member.get_fullname()})",
                request=self.request,
            )


@extend_schema_view(
    list=extend_schema(summary="List a team's weekly training slots"),
    create=extend_schema(summary="Add a weekly training slot (manager only)"),
    partial_update=extend_schema(summary="Edit a weekly training slot (manager only)"),
    update=extend_schema(summary="Replace a weekly training slot (manager only)"),
    destroy=extend_schema(summary="Delete a weekly training slot (manager only)"),
)
class TrainingSlotViewSet(TeamScopedViewMixin, viewsets.ModelViewSet):
    """Per-slot CRUD for a team's weekly training template.

    URL: /api/v1/teams/{team_pk}/training-slots/

    Each slot is saved on its own (add / edit / delete one slot persists
    immediately) — there is no bulk "save the template" step here. A slot
    carries weekday + hour_start/hour_end + an optional ``place`` (venue).
    Read: any strict team member. Write: owner/manager only.
    """

    serializer_class = TrainingSlotSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return TrainingSlot.objects.none()
        team = self.get_team()
        if not user_member_teams(self.request.user).filter(pk=team.pk).exists():
            return TrainingSlot.objects.none()
        return TrainingSlot.objects.filter(team=team).select_related("place")

    def _require_manager(self, team):
        if not team.is_managed_by(self.request.user):
            raise PermissionDenied(
                _("Only the team owner or managers can edit the training template.")
            )

    def _validate_place(self, team, place):
        # A slot's venue must be one of the team's linked places (consistent with
        # Event.place). Null place is allowed (falls back to the team default).
        if place is not None and not team.places.filter(pk=place.pk).exists():
            raise drf_serializers.ValidationError(
                {"place_id": _("The selected place is not one of this team's venues.")},
                code="place_not_in_team",
            )

    def _validate_sport(self, team, sport):
        # A slot's sport must be one of the team's sports.
        if sport is not None and not team.sports.filter(pk=sport.pk).exists():
            raise drf_serializers.ValidationError(
                {"sport_id": _("The selected sport is not one of this team's sports.")},
                code="sport_not_in_team",
            )

    def perform_create(self, serializer):
        team = self.get_team()
        self._require_manager(team)
        self._validate_place(team, serializer.validated_data.get("place"))
        sport = serializer.validated_data.get("sport")
        self._validate_sport(team, sport)
        serializer.save(team=team, sport=sport or team.default_sport)

    def perform_update(self, serializer):
        team = serializer.instance.team
        self._require_manager(team)
        self._validate_place(team, serializer.validated_data.get("place"))
        self._validate_sport(team, serializer.validated_data.get("sport"))
        serializer.save()

    def perform_destroy(self, instance):
        self._require_manager(instance.team)
        instance.delete()

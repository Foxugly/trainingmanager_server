import base64
import binascii
import re

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import translation
from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import serializers as drf_serializers
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from tools.exceptions import TeamQuotaExceeded
from tools.throttling import AIReviewThrottle

from ..models import Team
from ..permissions import IsTeamManagerOrReadOnly
from ..queries import user_member_teams, user_visible_teams
from ..serializers import (
    ReviewBlockRequestSerializer,
    ReviewBlockResponseSerializer,
    RosterHistoryResponseSerializer,
    RotiDriftResponseSerializer,
    RsvpReliabilityResponseSerializer,
    TeamSerializer,
    TeamStatsSerializer,
    TrainingTemplateSerializer,
)
from ..stats import assemble_stats, parse_window, roti_drift, rsvp_reliability


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
        from ..ai_review import review_training_block

        team = self.get_object()
        self._require_manager(
            team, _("Only the team owner or managers can request a training review.")
        )

        body = ReviewBlockRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        date_from, date_to, period = self._window(request)

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
            "period": period,
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
        self._require_manager(
            team, _("Only the team owner or managers can view the roster history.")
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
        self._require_manager(
            team, _("Only the team owner or managers can view RSVP reliability.")
        )
        date_from, date_to, period = self._window(request)
        entries = rsvp_reliability(team, date_from, date_to)
        return Response(
            RsvpReliabilityResponseSerializer({"period": period, "entries": entries}).data
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
        self._require_manager(team, _("Only the team owner or managers can view ROTI drift."))
        date_from, date_to, period = self._window(request)
        result = roti_drift(team, date_from, date_to)
        return Response(RotiDriftResponseSerializer({"period": period, **result}).data)

    def _require_manager(self, team, message):
        """Raise 403 unless the caller owns/manages ``team``. Shared by the
        manager-only read actions (stats aggregate, roster-history,
        rsvp-reliability, roti-drift, review-block)."""
        if team is None or not team.is_managed_by(self.request.user):
            raise PermissionDenied(message)

    @staticmethod
    def _window(request):
        """``parse_window`` + the ``{from, to}`` period dict the windowed
        responses wrap their payload in."""
        date_from, date_to = parse_window(request)
        return date_from, date_to, {"from": date_from, "to": date_to}

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

        # Managers may review any member who was EVER on the team (active or
        # departed) — end-of-season review of someone who has since left must
        # still work. A non-manager (athlete) is restricted to an active
        # membership of their own below.
        membership_qs = team.memberships.filter(member_id=member_id)
        if not is_manager:
            membership_qs = membership_qs.filter(left_at__isnull=True)
        membership = membership_qs.select_related("member").order_by("-joined_at").first()
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

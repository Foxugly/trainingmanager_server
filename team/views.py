import datetime
import logging

from django.conf import settings as dj_settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import models, transaction
from django.db.models import Count, F, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone, translation
from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers as drf_serializers
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tools.exceptions import TeamQuotaExceeded
from tools.openapi import INCLUDE_INACTIVE_PARAM

from .models import Team, TeamInvitation, TeamJoinRequest, TeamMembership
from .permissions import (
    IsJoinRequestParticipant,
    IsTeamManagerOrReadOnly,
    IsTrainer,
)
from .queries import managed_teams, user_visible_teams
from .serializers import (
    CompleteInvitationSerializer,
    CreateInvitationSerializer,
    CreateJoinRequestSerializer,
    JoinMagicCancelledResponseSerializer,
    JoinMagicErrorSerializer,
    TeamInvitationSerializer,
    TeamJoinRequestMagicActionPostSerializer,
    TeamJoinRequestMagicActionResponseSerializer,
    TeamJoinRequestSerializer,
    TeamMembershipSerializer,
    TeamSerializer,
    TeamStatsSerializer,
    ValidateInvitationSerializer,
)

logger = logging.getLogger(__name__)

# Default / clamp bounds for the stats endpoint date-range window.
STATS_DEFAULT_DAYS = 84  # 12 weeks; used when from/to are absent.
STATS_MAX_SPAN_DAYS = 731  # ~2 years; the window is clamped to this span.


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
            .select_related("sport", "owner")
            .prefetch_related("managers")
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
        from event.models import Event

        team = self.get_object()
        is_manager = team.is_managed_by(request.user)

        # Resolve the optional per-athlete scope + enforce permissions.
        scope_member = self._resolve_member_scope(request, team, is_manager)

        date_from, date_to = self._parse_window(request)

        # All of this team's events in the window. Filtering on the related
        # program.team keeps the scope strict.
        events = list(
            Event.objects.filter(
                refer_program__team=team,
                date__isnull=False,
                date__gte=date_from,
                date__lte=date_to,
            )
            .order_by("date", "id")
            .values("id", "name", "date", "total")
        )
        event_ids = [e["id"] for e in events]

        # Expected head-count for attendance "total": current active athlete
        # members of the team. Documented simplification: the same expected
        # roster applies to every session in the window (we do not reconstruct
        # the historical roster per event date).
        active_members = list(
            team.memberships.filter(left_at__isnull=True)
            .select_related("member")
            .values(
                "member_id",
                "member__firstname",
                "member__lastname",
            )
        )
        member_names = {
            m["member_id"]: (
                f"{m['member__firstname']} {m['member__lastname']}".strip()
            )
            for m in active_members
        }
        expected_per_session = len(active_members)

        member_id = scope_member["id"] if scope_member else None

        payload = {
            "period": {"from": date_from, "to": date_to},
            "member": scope_member,
            "attendance": self._attendance_stats(
                event_ids, events, member_names, expected_per_session, member_id
            ),
            "volume": self._volume_stats(event_ids, events, member_names, member_id),
            "intensity": self._intensity_stats(event_ids, member_id),
        }
        return Response(TeamStatsSerializer(payload).data)

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

    @staticmethod
    def _parse_window(request):
        """Parse the [from, to] date window from query params.

        Defaults: both absent -> to=today, from=today-84d. A single bound
        fills the other (to defaults today; from defaults to-84d). Malformed
        dates -> 400. from > to -> 400. The span is clamped to
        STATS_MAX_SPAN_DAYS by pulling `from` forward.
        """
        today = timezone.localdate()

        def _parse(value, field):
            if value is None or value == "":
                return None
            try:
                return datetime.date.fromisoformat(value)
            except (TypeError, ValueError):
                raise drf_serializers.ValidationError(
                    {field: _("Invalid date. Use ISO format YYYY-MM-DD.")},
                    code="invalid_date",
                )

        raw_from = request.query_params.get("from")
        raw_to = request.query_params.get("to")

        date_from = _parse(raw_from, "from")
        date_to = _parse(raw_to, "to")

        if date_to is None:
            date_to = today
        if date_from is None:
            date_from = date_to - datetime.timedelta(days=STATS_DEFAULT_DAYS)

        if date_from > date_to:
            raise drf_serializers.ValidationError(
                {"from": _("`from` must be on or before `to`.")},
                code="invalid_range",
            )

        # Clamp the span to a sane maximum by pulling `from` forward.
        if (date_to - date_from).days > STATS_MAX_SPAN_DAYS:
            date_from = date_to - datetime.timedelta(days=STATS_MAX_SPAN_DAYS)

        return date_from, date_to

    @staticmethod
    def _attendance_stats(
        event_ids, events, member_names, expected_per_session, member_id=None
    ):
        """Attendance present-counts grouped per event and per member.

        "present" = Attendance rows whose status.code == 'present'.

        Team aggregate (member_id is None): "total" per session = number of
        currently-active athlete members; by_member spans all members.

        Per-athlete scope (member_id set): by_session is that member's
        personal timeline (present 0/1, total 1 per session), team_rate is
        their overall present rate, by_member is the single scoped member.
        """
        from attendance.models import Attendance

        if not event_ids:
            return {"team_rate": None, "by_session": [], "by_member": []}

        if member_id is not None:
            return TeamViewSet._attendance_stats_member(
                event_ids, events, member_names, member_id
            )

        # present count per event
        per_event = dict(
            Attendance.objects.filter(
                event_id__in=event_ids, status__code="present"
            )
            .values_list("event_id")
            .annotate(n=Count("id"))
            .values_list("event_id", "n")
        )

        by_session = []
        total_present = 0
        for e in events:
            present = per_event.get(e["id"], 0)
            total_present += present
            total = expected_per_session
            rate = (present / total) if total else None
            by_session.append(
                {
                    "event_id": e["id"],
                    "name": e["name"],
                    "date": e["date"],
                    "present": present,
                    "total": total,
                    "rate": rate,
                }
            )

        total_expected = expected_per_session * len(events)
        team_rate = (total_present / total_expected) if total_expected else None

        # per-member present count + last present date across the window
        present_rows = (
            Attendance.objects.filter(
                event_id__in=event_ids, status__code="present"
            )
            .values("member_id")
            .annotate(
                present=Count("id"),
                last_present_date=models.Max("event__date"),
            )
        )
        present_by_member = {r["member_id"]: r for r in present_rows}

        session_count = len(events)
        by_member = []
        for member_id_, name in member_names.items():
            row = present_by_member.get(member_id_)
            present = row["present"] if row else 0
            last_present = row["last_present_date"] if row else None
            rate = (present / session_count) if session_count else None
            by_member.append(
                {
                    "member_id": member_id_,
                    "name": name,
                    "present": present,
                    "total": session_count,
                    "rate": rate,
                    "last_present_date": last_present,
                }
            )
        by_member.sort(key=lambda m: m["name"].lower())

        return {
            "team_rate": team_rate,
            "by_session": by_session,
            "by_member": by_member,
        }

    @staticmethod
    def _attendance_stats_member(event_ids, events, member_names, member_id):
        """Per-athlete attendance: personal timeline + overall rate."""
        from attendance.models import Attendance

        present_event_ids = set(
            Attendance.objects.filter(
                event_id__in=event_ids,
                status__code="present",
                member_id=member_id,
            ).values_list("event_id", flat=True)
        )

        by_session = []
        total_present = 0
        last_present_date = None
        for e in events:
            present = 1 if e["id"] in present_event_ids else 0
            total_present += present
            if present:
                if last_present_date is None or e["date"] > last_present_date:
                    last_present_date = e["date"]
            by_session.append(
                {
                    "event_id": e["id"],
                    "name": e["name"],
                    "date": e["date"],
                    "present": present,
                    "total": 1,
                    "rate": float(present),
                }
            )

        session_count = len(events)
        team_rate = (total_present / session_count) if session_count else None

        name = member_names.get(member_id) or f"member #{member_id}"
        by_member = [
            {
                "member_id": member_id,
                "name": name,
                "present": total_present,
                "total": session_count,
                "rate": team_rate,
                "last_present_date": last_present_date,
            }
        ]

        return {
            "team_rate": team_rate,
            "by_session": by_session,
            "by_member": by_member,
        }

    @staticmethod
    def _volume_stats(event_ids, events, member_names, member_id=None):
        """Training volume = sum of Event.total over the window.

        Team aggregate (member_id is None): total_distance counts each
        session once; by_week buckets by ISO Monday; by_member attributes a
        session's full distance to every member present at it.

        Per-athlete scope (member_id set): total_distance / by_week only
        count sessions where THAT member was present (their personal volume);
        by_member is the single scoped member.
        """
        from attendance.models import Attendance

        if not event_ids:
            return {"total_distance": 0, "by_week": [], "by_member": []}

        if member_id is not None:
            present_event_ids = set(
                Attendance.objects.filter(
                    event_id__in=event_ids,
                    status__code="present",
                    member_id=member_id,
                ).values_list("event_id", flat=True)
            )
            scoped_events = [e for e in events if e["id"] in present_event_ids]
            total_distance = sum(e["total"] for e in scoped_events)
            by_week = TeamViewSet._bucket_by_week(scoped_events)
            name = member_names.get(member_id) or f"member #{member_id}"
            by_member = (
                [{"member_id": member_id, "name": name, "distance": total_distance}]
                if total_distance
                else [{"member_id": member_id, "name": name, "distance": 0}]
            )
            return {
                "total_distance": total_distance,
                "by_week": by_week,
                "by_member": by_member,
            }

        total_distance = sum(e["total"] for e in events)
        by_week = TeamViewSet._bucket_by_week(events)

        # by_member — attribute each present session's distance to the member.
        event_total = {e["id"]: e["total"] for e in events}
        present_links = Attendance.objects.filter(
            event_id__in=event_ids, status__code="present"
        ).values_list("member_id", "event_id")

        member_distance: dict[int, int] = {}
        for m_id, event_id in present_links:
            member_distance[m_id] = member_distance.get(m_id, 0) + event_total.get(
                event_id, 0
            )

        by_member = [
            {
                "member_id": m_id,
                "name": member_names.get(m_id) or f"member #{m_id}",
                "distance": dist,
            }
            for m_id, dist in member_distance.items()
        ]
        by_member.sort(key=lambda m: (-m["distance"], m["name"].lower()))

        return {
            "total_distance": total_distance,
            "by_week": by_week,
            "by_member": by_member,
        }

    @staticmethod
    def _bucket_by_week(events):
        """Bucket events' distance by the ISO Monday of their date (Python,
        DB-agnostic, small N of sessions)."""
        week_buckets: dict[datetime.date, int] = {}
        for e in events:
            d = e["date"]
            monday = d - datetime.timedelta(days=d.weekday())
            week_buckets[monday] = week_buckets.get(monday, 0) + e["total"]
        return [
            {"week_start": wk, "distance": dist}
            for wk, dist in sorted(week_buckets.items())
        ]

    @staticmethod
    def _intensity_stats(event_ids, member_id=None):
        """Distance per energy zone across the window's exercises.

        zone distance = sum(exercise.distance * exercise.repetition *
        round.count) grouped by exercise.energysegment.abv. The localized
        segment description (modeltranslation, active request language) is
        returned as `label`. Ordered by abv (Z0..Z7).

        Per-athlete scope (member_id set): restricts to the sessions THAT
        member was present at (attributing session exercises to the member).
        """
        from exercise.models import Exercise

        if not event_ids:
            return {"by_segment": []}

        if member_id is not None:
            from attendance.models import Attendance

            event_ids = list(
                Attendance.objects.filter(
                    event_id__in=event_ids,
                    status__code="present",
                    member_id=member_id,
                ).values_list("event_id", flat=True)
            )
            if not event_ids:
                return {"by_segment": []}

        # Exercises reachable from the window's events:
        #   Event -(M2M)- Round -(M2M)- Exercise
        # Multiply the exercise's own reps*distance by the parent round's
        # count. The same exercise can appear under multiple rounds/events;
        # each occurrence contributes (this is intentional — total training
        # load over the period).
        rows = (
            Exercise.objects.filter(
                round__event__id__in=event_ids,
                energysegment__isnull=False,
            )
            .annotate(
                seg_abv=F("energysegment__abv"),
                contrib=F("distance") * F("repetition") * F("round__count"),
            )
            .values("seg_abv", "energysegment_id")
            .annotate(distance=Sum("contrib"))
        )

        # Resolve localized labels once per segment id.
        from exercise.models import EnergySegment

        seg_ids = {r["energysegment_id"] for r in rows}
        labels = {
            s.id: s.description
            for s in EnergySegment.objects.filter(id__in=seg_ids)
        }

        by_segment = [
            {
                "abv": r["seg_abv"],
                "label": labels.get(r["energysegment_id"]),
                "distance": r["distance"] or 0,
            }
            for r in rows
        ]
        by_segment.sort(key=lambda s: s["abv"])
        return {"by_segment": by_segment}


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
class TeamMembershipViewSet(viewsets.ModelViewSet):
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

    def get_team(self):
        team_pk = self.kwargs.get("team_pk")
        if not team_pk:
            return None
        return get_object_or_404(Team, pk=team_pk)

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

from datetime import date as _date
from datetime import datetime as _datetime

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from event.models import Event
from team.queries import managed_teams, user_member_teams
from tools.exceptions import NotAManagerDenied
from tools.openapi import INCLUDE_INACTIVE_PARAM
from tools.throttling import AIPlanGenerationThrottle

from .ai import generate_plan
from .models import Program
from .serializers import GeneratePlanRequestSerializer, ProgramSerializer


def _parse_hhmm(value):
    """Parse an AI-supplied 'HH:MM' string into a time, or None if absent/invalid.

    Defensive: the AI is asked for 24h HH:MM but a bad value must never break
    the whole plan creation — we just drop the time for that session.
    """
    if not value:
        return None
    try:
        return _datetime.strptime(value.strip(), "%H:%M").time()
    except (ValueError, AttributeError):
        return None


@extend_schema_view(
    list=extend_schema(parameters=[INCLUDE_INACTIVE_PARAM]),
    retrieve=extend_schema(parameters=[INCLUDE_INACTIVE_PARAM]),
    update=extend_schema(parameters=[INCLUDE_INACTIVE_PARAM]),
    partial_update=extend_schema(parameters=[INCLUDE_INACTIVE_PARAM]),
    destroy=extend_schema(
        summary="Soft delete program (manager of team only)",
        description="Sets is_active=False; does not hard delete.",
    ),
)
class ProgramViewSet(viewsets.ModelViewSet):
    """CRUD complet pour Program, scopé par team.

    Soft-delete convention: DELETE flips is_active=False (no hard delete).
    Default queryset hides is_active=False. ?include_inactive=true is honored
    for staff (sees all inactives across visible teams) and for managers
    (sees inactives of teams they manage).
    """

    serializer_class = ProgramSerializer
    filterset_fields = ["name", "date_start", "date_end", "team", "is_active"]
    search_fields = ["name"]
    ordering_fields = ["name", "date_start", "date_end"]
    ordering = ["name"]

    def get_queryset(self):
        user = self.request.user
        base = (
            Program.objects.filter(team__in=user_member_teams(user))
            .select_related("team", "team__owner")
            .prefetch_related("events")
        )
        include_inactive = self.request.query_params.get("include_inactive") == "true"
        if include_inactive and user.is_authenticated and user.is_staff:
            return base
        if include_inactive and user.is_authenticated:
            return base.filter(Q(is_active=True) | Q(team__in=managed_teams(user)))
        return base.filter(is_active=True)

    def _check_team_write(self, team):
        if team is None or not managed_teams(self.request.user).filter(pk=team.pk).exists():
            raise PermissionDenied(_("You do not manage this team."))

    def perform_create(self, serializer):
        self._check_team_write(serializer.validated_data.get("team"))
        serializer.save()

    def perform_update(self, serializer):
        self._check_team_write(serializer.validated_data.get("team", serializer.instance.team))
        serializer.save()

    def perform_destroy(self, instance):
        self._check_team_write(instance.team)
        instance.is_active = False
        instance.save(update_fields=["is_active"])

    @extend_schema(
        request=GeneratePlanRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="GeneratePlanResponse",
                    fields={
                        "created_count": serializers.IntegerField(),
                        "deleted_count": serializers.IntegerField(),
                        "rationale": serializers.CharField(),
                        "model": serializers.CharField(),
                        "tokens_used": inline_serializer(
                            name="GeneratePlanTokensUsed",
                            fields={
                                "input": serializers.IntegerField(),
                                "output": serializers.IntegerField(),
                            },
                        ),
                    },
                ),
                description="Plan generated successfully",
            ),
            400: OpenApiResponse(description="Invalid request data"),
            403: OpenApiResponse(description="Not a manager of this program team"),
            500: OpenApiResponse(description="AI configuration error"),
            502: OpenApiResponse(description="AI service error"),
        },
        description="Generate a training plan with AI for the given Program.",
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="generate-events",
        throttle_classes=[AIPlanGenerationThrottle],
    )
    def generate_events(self, request, pk=None):
        """POST /programs/{id}/generate-events/ — Claude-generated session list."""
        program = self.get_object()

        if not program.team.is_managed_by(request.user):
            raise NotAManagerDenied(_("You must be owner or manager of this program's team."))

        serializer = GeneratePlanRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Resolve the team's weekly training template. When it has slots, the
        # frequency is derived from the number of slots (any supplied value is
        # ignored) and the slots + default_pool drive the AI placement. With no
        # template, frequency_per_week must be supplied (else 400).
        slots = list(
            program.team.training_slots.values("weekday", "hour_start", "hour_end")
        )
        if slots:
            frequency_per_week = len(slots)
            default_pool = program.team.default_pool
        else:
            frequency_per_week = data.get("frequency_per_week")
            if frequency_per_week is None:
                raise serializers.ValidationError(
                    {"frequency_per_week": _("This team has no training template; "
                                             "frequency_per_week is required.")},
                    code="frequency_required",
                )
            default_pool = ""

        ai_result = generate_plan(
            program=program,
            date_start=data["date_start"],
            date_end=data["date_end"],
            frequency_per_week=frequency_per_week,
            description=data["description"],
            additional_prompt=data.get("additional_prompt", ""),
            user=request.user if request.user.is_authenticated else None,
            slots=slots,
            default_pool=default_pool,
        )

        with transaction.atomic():
            created_count, deleted_count = self._apply_overlap_strategy(
                program=program,
                new_events_data=ai_result["events"],
                strategy=data["overlap_strategy"],
                date_start=data["date_start"],
                date_end=data["date_end"],
            )

            program.frequency_per_week = frequency_per_week
            program.description = data["description"]
            program.generated_by_ai = True
            program.ai_prompt = ai_result["prompt_sent"]
            program.ai_response = ai_result["rationale"]
            program.ai_generated_at = timezone.now()
            program.save()

        # After the plan is committed, notify the team's active athletes once
        # per generation (not per session). Guarded so a notify failure can
        # never undo or break a successful generation.
        self._notify_athletes_plan_generated(program, created_count)

        return Response(
            {
                "created_count": created_count,
                "deleted_count": deleted_count,
                "rationale": ai_result["rationale"],
                "model": ai_result["model"],
                "tokens_used": {
                    "input": ai_result["input_tokens"],
                    "output": ai_result["output_tokens"],
                },
            },
            status=status.HTTP_200_OK,
        )

    def _notify_athletes_plan_generated(self, program, created_count):
        """Notify each active athlete of the program's team that a plan was
        generated. One efficient query for the team's active memberships;
        members without a linked user are skipped, and the actor is never
        notified (``notify`` enforces that). Fully guarded — a notification
        failure must never break a successful generation."""
        try:
            from member.models import Member
            from notifications.models import NotificationType
            from notifications.services import notify

            actor = self.request.user
            members = (
                Member.objects.filter(
                    memberships__team=program.team,
                    memberships__left_at__isnull=True,
                    user__isnull=False,
                )
                .select_related("user")
                .distinct()
            )
            url = f"/programs/{program.id}"
            for member in members:
                notify(
                    member.user,
                    NotificationType.PLAN_GENERATED,
                    title=_("New training plan scheduled"),
                    body=_("%(n)s sessions added to %(name)s")
                    % {"n": created_count, "name": program.name},
                    url=url,
                    actor=actor,
                )
        except Exception:  # pragma: no cover - defensive: never break generation
            import logging

            logging.getLogger(__name__).exception(
                "Failed to send plan-generated notifications"
            )

    def _apply_overlap_strategy(self, *, program, new_events_data, strategy, date_start, date_end):
        deleted_count = 0
        if strategy == "replace":
            existing = Event.objects.filter(
                refer_program=program,
                date__gte=date_start,
                date__lte=date_end,
            )
            # I4: single-pass delete that returns an exact count, instead of
            # count() + delete() which could mismatch under concurrent inserts.
            # We pick the per-model count rather than the cascade total so the
            # response reflects "Events replaced", not "rows deleted total"
            # (Attendance.event is CASCADE and would inflate the number).
            _total, per_model = existing.delete()
            deleted_count = per_model.get("event.Event", 0)

        # Resolve each AI-returned venue name to a managed Place: reuse an
        # existing one (case-insensitively) or create it on the fly so a venue
        # the coach named in the prompt but hadn't registered becomes a real,
        # reusable Lieu. Cache keyed by casefolded name avoids duplicates within
        # the batch and a query per session.
        place_cache = {
            p.name.casefold(): p for p in program.team.places.all()
        }

        created_count = 0
        for ev_data in new_events_data:
            ev_date = _date.fromisoformat(ev_data["date"])

            if strategy == "add_only":
                if Event.objects.filter(refer_program=program, date=ev_date).exists():
                    continue

            place = self._resolve_place(
                program.team, ev_data.get("location"), place_cache
            )
            Event.objects.create(
                refer_program=program,
                name=ev_data["name"][:100],
                goal=ev_data["goal"][:100],
                date=ev_date,
                hour_start=_parse_hhmm(ev_data.get("hour_start")),
                hour_end=_parse_hhmm(ev_data.get("hour_end")),
                location=(place.name if place else (ev_data.get("location") or ""))[:255],
                place=place,
                color=ev_data["color"],
                total=ev_data["total_distance"],
            )
            created_count += 1

        return created_count, deleted_count

    def _resolve_place(self, team, location, cache):
        """Map an AI-returned venue name to the team's Place, creating it if new.

        Returns ``None`` for an empty location. Matching is case-insensitive and
        whitespace-trimmed; ``cache`` (casefolded name -> Place) is updated in
        place so a venue named several times in one plan is created only once.
        """
        name = (location or "").strip()
        if not name:
            return None
        key = name.casefold()
        place = cache.get(key)
        if place is None:
            from place.models import Place

            place = Place.objects.create(sport=team.sport, name=name[:255])
            team.places.add(place)
            cache[key] = place
        return place

from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from round.models import Round
from team.queries import managed_teams, user_member_teams
from tools.exceptions import NotAManagerDenied, NotAuthorizedEventDenied
from tools.serializers import TokensUsedSerializer
from tools.throttling import AIExplainThrottle, AITrainingGenerationThrottle
from tools.validators import validate_reorder_ids

from .ai import explain_session as ai_explain_session
from .ai import generate_freeform_training as ai_generate_freeform_training
from .ai import generate_training as ai_generate_training
from .models import Event
from .serializers import (
    DuplicateEventRequestSerializer,
    EventDebriefSerializer,
    EventSerializer,
    EventShareRequestSerializer,
    EventTemplateSerializer,
    GenerateTrainingRequestSerializer,
    InstantiateTemplateRequestSerializer,
    ReorderRoundsRequestSerializer,
    SaveAsTemplateRequestSerializer,
)

# Fields copied verbatim from the source Event onto each duplicate. Excludes
# id / created_at / updated_at (auto), members (per-session attendance — left
# empty by design), rounds (deep-copied separately), and date (set per copy).
# The AI lineage fields are preserved as-is so a duplicate of an AI-generated
# session is still attributed to that generation.
_EVENT_COPY_FIELDS = (
    "name",
    "goal",
    "color",
    "hour_start",
    "hour_end",
    "total",
    "total_target",
    "location",
    "equipment",
    "vis_distance",
    "vis_goal",
    "vis_rounds",
    "training_type",
    "training_richtext",
    "refer_program",
    "generated_by_ai",
    "ai_prompt",
    "ai_response",
    "ai_generated_at",
)


def _duplicate_event(source, target_date):
    """Create one independent copy of ``source`` on ``target_date``.

    Scalar fields are copied verbatim (see ``_EVENT_COPY_FIELDS``); ``date`` is
    set to the target.

    Rounds vs. exercises differ deliberately:
      * Rounds are per-event in practice (generate_training mints fresh Round
        rows for every event; they are never shared), so we DEEP-COPY each
        source Round into a brand-new Round attached only to the copy. This
        keeps the copy's rounds editable/deletable in full isolation from the
        source.
      * Exercises are a shared, de-duplicated library (generate_training reuses
        them via get_or_create), so we RE-LINK the same Exercise rows onto the
        new rounds rather than cloning them.

    Members (M2M) are intentionally NOT copied — attendance is per-session.

    Must be called inside a transaction.atomic() by the caller.
    """
    new_event = Event(date=target_date)
    for field in _EVENT_COPY_FIELDS:
        setattr(new_event, field, getattr(source, field))
    new_event.save()

    for src_round in source.rounds.all():
        new_round = Round.objects.create(
            order=src_round.order,
            count=src_round.count,
            t_start=src_round.t_start,
            t_break=src_round.t_break,
            sport=src_round.sport,
            language=src_round.language,
        )
        # Re-link the SAME shared Exercise rows (do not clone the library).
        new_round.exercises.set(src_round.exercises.all())
        new_event.rounds.add(new_round)

    return new_event


def _clone_rounds(source_rounds):
    """Deep-copy a sequence of Rounds into brand-new Round rows (re-linking the
    SAME shared Exercise library, like _duplicate_event). Returns the new list.
    Used by save-as-template (event→template) and instantiate (template→event)
    so neither side mutates the other's rounds."""
    cloned = []
    for src in source_rounds:
        new_round = Round.objects.create(
            order=src.order,
            count=src.count,
            t_start=src.t_start,
            t_break=src.t_break,
            sport=src.sport,
            language=src.language,
        )
        new_round.exercises.set(src.exercises.all())
        cloned.append(new_round)
    return cloned


class EventViewSet(viewsets.ModelViewSet):
    """CRUD complet pour Event, scopé par team du program."""

    serializer_class = EventSerializer
    filterset_fields = {
        # "in" lets the calendar fetch all selected programs' events in ONE
        # request (?refer_program__in=1,2,3) instead of one request per program.
        "refer_program": ["exact", "in"],
        "date": ["exact", "gte", "lte"],
        "color": ["exact"],
    }
    search_fields = ["name", "goal"]
    ordering_fields = ["date", "hour_start", "name", "id"]
    ordering = ["-date", "hour_start"]

    def get_queryset(self):
        qs = (
            Event.objects.filter(refer_program__team__in=user_member_teams(self.request.user))
            .select_related("refer_program", "refer_program__team", "place")
            .prefetch_related("rounds", "members", "equipment_items")
        )
        # On retrieve the serializer embeds rounds + their exercises
        # (rounds_detail) so the client loads a session in one request instead
        # of N round/exercise fetches — deep-prefetch only there to keep lists
        # light. The exercises are annotated with _usage_count so the
        # ExerciseSerializer reads it from the prefetch instead of issuing one
        # COUNT(round) per exercise (N+1 on a full session).
        if self.action == "retrieve":
            from django.db.models import Count, Prefetch

            from exercise.models import Exercise

            qs = qs.prefetch_related(
                "rounds__sport",
                Prefetch(
                    "rounds__exercises",
                    queryset=Exercise.objects.annotate(_usage_count=Count("round")).select_related(
                        "modality__sport", "energysegment__energysystem"
                    ),
                ),
            )
        return qs

    def _check_program_write(self, program):
        if program is None:
            raise PermissionDenied(_("refer_program is required."))
        if not managed_teams(self.request.user).filter(pk=program.team_id).exists():
            raise PermissionDenied(_("You do not manage the team of this program."))

    def _require_event_manager(self, event):
        """Raise 403 unless the caller owns/manages the event's team; return the
        team. Shared by the manager-only event actions."""
        team = event.team
        if team is None or not team.is_managed_by(self.request.user):
            raise NotAManagerDenied(_("You must be owner or manager of this event's team."))
        return team

    def perform_create(self, serializer):
        program = serializer.validated_data.get("refer_program")
        self._check_program_write(program)
        # Inherit each per-aspect visibility default from the event's team
        # unless the create payload explicitly set it. We key off the raw
        # request body because the serializer fields carry model defaults,
        # so validated_data alone cannot distinguish "omitted" from
        # "explicitly equal to the default".
        team = program.team if program is not None else None
        overrides = {}
        if team is not None:
            for aspect in ("vis_distance", "vis_goal", "vis_rounds"):
                if aspect not in self.request.data:
                    overrides[aspect] = getattr(team, aspect)
        serializer.save(**overrides)

    def perform_update(self, serializer):
        self._check_program_write(
            serializer.validated_data.get("refer_program", serializer.instance.refer_program)
        )
        serializer.save()

    @extend_schema(
        request=GenerateTrainingRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="GenerateTrainingResponse",
                    fields={
                        # Structured-only counts: absent on the freeform branch,
                        # which returns just rationale/model/tokens_used.
                        "rounds_created": serializers.IntegerField(required=False),
                        "exercises_created": serializers.IntegerField(required=False),
                        "exercises_reused": serializers.IntegerField(required=False),
                        "rationale": serializers.CharField(),
                        "model": serializers.CharField(),
                        "tokens_used": TokensUsedSerializer(),
                    },
                ),
                description="Training session generated successfully",
            ),
            400: OpenApiResponse(description="Invalid request body"),
            403: OpenApiResponse(description="Not a manager of this event team"),
            409: OpenApiResponse(
                description=(
                    "Conflict. `body.code` is one of: `event_has_rounds` "
                    "(event already has rounds — remove them before "
                    "regenerating), `event_has_training` (freeform event "
                    "already has free-text content — clear it before "
                    "regenerating) or `event_in_past` (the session date is in "
                    "the past — training can only be generated for a future or "
                    "unscheduled session)."
                )
            ),
            500: OpenApiResponse(description="AI configuration error"),
            502: OpenApiResponse(description="AI service error"),
        },
        description=(
            "Generate detailed Rounds and Exercises with AI for an Event. "
            "Optionally accepts an `additional_prompt` (max 2000 chars) "
            "appended to the LLM user prompt after the structured context."
        ),
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="generate-training",
        throttle_classes=[AITrainingGenerationThrottle],
    )
    def generate_training(self, request, pk=None):
        """POST /api/v1/events/{id}/generate-training/ — Claude-generated rounds."""
        from .services import apply_generated_training

        event = self.get_object()

        self._require_event_manager(event)

        # Future-only: a scheduled session whose date is already in the past
        # (judged in the team's timezone) cannot be (re)generated. An
        # unscheduled session (date is None) is always allowed.
        if event.date is not None:
            team_today = timezone.now().astimezone(event._team_tzinfo()).date()
            if event.date < team_today:
                return Response(
                    {
                        "code": "event_in_past",
                        "detail": _(
                            "You can only generate a training for a session in the future."
                        ),
                    },
                    status=status.HTTP_409_CONFLICT,
                )

        from tools.choices import TrainingType

        if event.training_type == TrainingType.FREEFORM:
            if event.training_richtext:
                return Response(
                    {"code": "event_has_training",
                     "detail": _("Event already has free-text content. Clear it before regenerating.")},
                    status=status.HTTP_409_CONFLICT,
                )
            body_serializer = GenerateTrainingRequestSerializer(data=request.data)
            body_serializer.is_valid(raise_exception=True)
            additional_prompt = body_serializer.validated_data.get("additional_prompt", "")
            ai_result = ai_generate_freeform_training(
                event=event,
                user=request.user if request.user.is_authenticated else None,
                additional_prompt=additional_prompt,
            )
            from tools.html_sanitizer import sanitize_html

            event.training_richtext = sanitize_html(ai_result["html"])
            event.generated_by_ai = True
            event.ai_prompt = ai_result["prompt_sent"]
            event.ai_response = ai_result["rationale"]
            event.ai_generated_at = timezone.now()
            event.save()
            return Response(
                {"rationale": ai_result["rationale"], "model": ai_result["model"],
                 "tokens_used": {"input": ai_result["input_tokens"], "output": ai_result["output_tokens"]}},
                status=status.HTTP_200_OK,
            )

        if event.rounds.exists():
            return Response(
                {
                    "code": "event_has_rounds",
                    "detail": _("Event already has rounds. Remove them before regenerating."),
                },
                status=status.HTTP_409_CONFLICT,
            )

        # A structured session is generated to approach a target volume; without
        # one the AI has nothing to size the session against. Require the coach
        # to set the target volume first (issue #9).
        if not event.total_target:
            return Response(
                {
                    "code": "event_without_volume",
                    "detail": _(
                        "Set a target volume for this session before generating its training."
                    ),
                },
                status=status.HTTP_409_CONFLICT,
            )

        body_serializer = GenerateTrainingRequestSerializer(data=request.data)
        body_serializer.is_valid(raise_exception=True)
        additional_prompt = body_serializer.validated_data.get("additional_prompt", "")

        ai_result = ai_generate_training(
            event=event,
            user=request.user if request.user.is_authenticated else None,
            additional_prompt=additional_prompt,
        )

        # Stamp Rounds with the SESSION's sport — the same sport the generator
        # was scoped to (event/ai.py) — not the team's default. On a multi-sport
        # team an event whose sport is a non-default sport would otherwise get
        # rounds mis-attributed to the default sport.
        team_sport = event.sport or event.refer_program.team.default_sport
        team_language = event.refer_program.team.language

        counts = apply_generated_training(
            event,
            ai_result,
            team_sport=team_sport,
            team_language=team_language,
        )

        return Response(
            {
                "rounds_created": counts["created_rounds"],
                "exercises_created": counts["created_exercises"],
                "exercises_reused": counts["reused_exercises"],
                "rationale": ai_result["rationale"],
                "model": ai_result["model"],
                "tokens_used": {
                    "input": ai_result["input_tokens"],
                    "output": ai_result["output_tokens"],
                },
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        request=ReorderRoundsRequestSerializer,
        responses={
            204: OpenApiResponse(description="Rounds reordered"),
            400: OpenApiResponse(
                description=(
                    "Validation error. `body.code` is one of: `empty_list`, "
                    "`duplicate_id`, `scope_mismatch`, `incomplete_reorder`."
                )
            ),
            403: OpenApiResponse(description="Not a manager of this event team"),
        },
        description=(
            "Atomically reorder the Rounds attached to this Event. "
            "`round_ids` must contain exactly the IDs of the Rounds currently "
            "attached, in the desired final order. Round.order is set to "
            "1..N matching list position, in a single transaction."
        ),
    )
    @action(detail=True, methods=["post"], url_path="rounds/reorder")
    def rounds_reorder(self, request, pk=None):
        event = self.get_object()
        if not event.refer_program or not event.refer_program.team.is_managed_by(request.user):
            raise NotAuthorizedEventDenied(
                _("You must manage this event's team to reorder its rounds.")
            )

        body_serializer = ReorderRoundsRequestSerializer(data=request.data)
        body_serializer.is_valid(raise_exception=True)
        round_ids = body_serializer.validated_data["round_ids"]

        validate_reorder_ids(
            round_ids,
            event.rounds.values_list("id", flat=True),
            field_name="round_ids",
            container_label="event",
        )

        with transaction.atomic():
            for index, round_id in enumerate(round_ids, start=1):
                Round.objects.filter(pk=round_id).update(order=index)

        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        request=DuplicateEventRequestSerializer,
        responses={
            201: OpenApiResponse(
                response=inline_serializer(
                    name="DuplicateEventResponse",
                    fields={
                        "created": inline_serializer(
                            name="DuplicateEventCreated",
                            many=True,
                            fields={
                                "id": serializers.IntegerField(),
                                "date": serializers.DateField(),
                            },
                        ),
                    },
                ),
                description="Session(s) duplicated successfully",
            ),
            400: OpenApiResponse(description="Invalid request body"),
            403: OpenApiResponse(description="Not a manager of this event team"),
            404: OpenApiResponse(description="Event not found / not in scope"),
        },
        description=(
            "Duplicate this training session onto a new date, optionally "
            "repeating weekly. The copy deep-copies the source's Rounds into "
            "fresh Round rows (re-linking the shared Exercise library) so each "
            "session is fully independent; per-session members are NOT copied. "
            "With `repeat_weekly=true` and `occurrences=N`, N sessions are "
            "created on `date`, `date`+7d, ..., `date`+7*(N-1)d. With "
            "`repeat_weekly=false` exactly one copy is created (occurrences is "
            "forced to 1). Manager/owner of the event's team only."
        ),
    )
    @action(detail=True, methods=["post"], url_path="duplicate")
    def duplicate(self, request, pk=None):
        """POST /api/v1/events/{id}/duplicate/ — copy a session, optional weekly repeat."""
        event = self.get_object()

        self._require_event_manager(event)

        body_serializer = DuplicateEventRequestSerializer(data=request.data)
        body_serializer.is_valid(raise_exception=True)
        start_date = body_serializer.validated_data["date"]
        repeat_weekly = body_serializer.validated_data["repeat_weekly"]
        # Non-recurring duplication is always a single copy, regardless of any
        # occurrences value the client supplied.
        occurrences = body_serializer.validated_data["occurrences"] if repeat_weekly else 1

        created = []
        with transaction.atomic():
            for k in range(occurrences):
                target_date = start_date + timedelta(days=7 * k)
                new_event = _duplicate_event(event, target_date)
                created.append({"id": new_event.pk, "date": target_date})

        return Response({"created": created}, status=status.HTTP_201_CREATED)

    @extend_schema(
        request=EventShareRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="EventShareResponse",
                    fields={
                        "is_public": serializers.BooleanField(),
                        "public_token": serializers.CharField(allow_null=True),
                        "public_url_path": serializers.CharField(
                            allow_null=True,
                            help_text="Frontend route, e.g. /s/e/<token>. Null when not shared.",
                        ),
                    },
                ),
                description="Sharing state updated.",
            ),
            400: OpenApiResponse(description="Invalid request body"),
            403: OpenApiResponse(description="Not a manager of this event team"),
            409: OpenApiResponse(
                description=(
                    "Public sharing is disabled for this team "
                    "(`body.code` == `public_sharing_disabled`)."
                )
            ),
        },
        description=(
            "Toggle the public read-only share link for this session. "
            "Enabling (is_public=true) requires the event's team to have "
            "public_sharing_enabled=true (else 409 public_sharing_disabled) and "
            "mints an unguessable token if absent. Disabling (is_public=false) "
            "keeps the token so re-enabling reuses the same URL. Manager/owner "
            "of the event's team only."
        ),
    )
    @action(detail=True, methods=["post"], url_path="share")
    def share(self, request, pk=None):
        """POST /api/v1/events/{id}/share/ — toggle a public read-only share link."""
        event = self.get_object()

        self._require_event_manager(event)

        body_serializer = EventShareRequestSerializer(data=request.data)
        body_serializer.is_valid(raise_exception=True)
        is_public = body_serializer.validated_data["is_public"]

        team = event.refer_program.team
        if is_public:
            if not team.public_sharing_enabled:
                return Response(
                    {
                        "code": "public_sharing_disabled",
                        "detail": _("Public sharing is disabled for this team."),
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            event.ensure_public_token()
            event.is_public = True
            event.save(update_fields=["is_public"])
        else:
            # Keep the token so a later re-enable reuses the same public URL.
            event.is_public = False
            event.save(update_fields=["is_public"])

        # Audit the share-state change (best-effort; never breaks the action).
        from audit.models import AuditAction
        from audit.services import audit_event

        audit_event(
            request,
            AuditAction.SESSION_SHARED if event.is_public else AuditAction.SESSION_UNSHARED,
            team=team,
            target_repr=f"Event #{event.id}",
        )

        token = event.get_public_token()
        return Response(
            {
                "is_public": event.is_public,
                "public_token": token,
                "public_url_path": f"/s/e/{token}" if (event.is_public and token) else None,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        operation_id="events_debrief_retrieve",
        summary="Consolidated post-session debrief (managers only)",
        description=(
            "One read with everything a coach reviews after a session: "
            "attendance (present/absent vs active roster), ROTI (average + "
            "count), RSVP (going/maybe/not_going/no_response) and the attachment "
            "count, plus the free-text `debrief` (edited via the normal event "
            "PATCH). Managers of the event's team only."
        ),
        responses={
            200: EventDebriefSerializer,
            403: OpenApiResponse(description="Not a manager of this event's team"),
        },
    )
    @action(detail=True, methods=["get"], url_path="debrief")
    def debrief(self, request, pk=None):
        """GET /events/{id}/debrief/ — consolidated post-session summary."""
        from django.contrib.contenttypes.models import ContentType
        from django.db.models import Avg, Count

        from attachment.models import Attachment
        from attendance.models import Attendance
        from roti.models import Roti
        from rsvp.models import Rsvp, RsvpStatus

        event = self.get_object()
        team = self._require_event_manager(event)

        present = Attendance.objects.filter(event=event, status__code="present").count()
        absent = Attendance.objects.filter(event=event, status__code="absent").count()
        total_active = team.memberships.filter(left_at__isnull=True).count()

        roti_agg = Roti.objects.filter(event=event).aggregate(avg=Avg("score"), n=Count("id"))

        rsvp_counts = {RsvpStatus.GOING: 0, RsvpStatus.MAYBE: 0, RsvpStatus.NOT_GOING: 0}
        for status_value, n in (
            Rsvp.objects.filter(event=event).values_list("status").annotate(n=Count("id"))
        ):
            if status_value in rsvp_counts:
                rsvp_counts[status_value] = n
        responded = sum(rsvp_counts.values())

        attachments_count = Attachment.objects.filter(
            content_type=ContentType.objects.get_for_model(Event), object_id=event.id
        ).count()

        payload = {
            "debrief": event.debrief,
            "attendance": {"present": present, "absent": absent, "total": total_active},
            "roti": {"average": roti_agg["avg"], "count": roti_agg["n"]},
            "rsvp": {
                "going": rsvp_counts[RsvpStatus.GOING],
                "maybe": rsvp_counts[RsvpStatus.MAYBE],
                "not_going": rsvp_counts[RsvpStatus.NOT_GOING],
                "no_response": max(0, total_active - responded),
            },
            "attachments_count": attachments_count,
        }
        return Response(EventDebriefSerializer(payload).data)

    @extend_schema(
        request=None,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="ExplainSessionResponse",
                    fields={
                        "athlete_brief": serializers.CharField(),
                        "model": serializers.CharField(),
                        "tokens_used": TokensUsedSerializer(),
                    },
                ),
                description="Athlete brief generated and stored on the event.",
            ),
            403: OpenApiResponse(description="Not a manager of this event's team"),
            500: OpenApiResponse(description="AI configuration error"),
            502: OpenApiResponse(description="AI service error"),
        },
        description=(
            "Generate a short, plain-language brief of this session FOR THE "
            "ATHLETES and store it on the event (ai_athlete_brief). Managers of "
            "the event's team only. The brief is shown to athletes only when "
            "they may see the goal (vis_goal). Throttled."
        ),
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="explain",
        throttle_classes=[AIExplainThrottle],
    )
    def explain(self, request, pk=None):
        """POST /events/{id}/explain/ — AI athlete-facing brief."""
        event = self.get_object()
        self._require_event_manager(event)

        ai_result = ai_explain_session(
            event=event,
            user=request.user if request.user.is_authenticated else None,
        )
        event.ai_athlete_brief = ai_result["athlete_brief"]
        event.ai_brief_generated_at = timezone.now()
        event.save(update_fields=["ai_athlete_brief", "ai_brief_generated_at", "updated_at"])

        return Response(
            {
                "athlete_brief": ai_result["athlete_brief"],
                "model": ai_result["model"],
                "tokens_used": {
                    "input": ai_result["input_tokens"],
                    "output": ai_result["output_tokens"],
                },
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        request=SaveAsTemplateRequestSerializer,
        responses={
            201: EventTemplateSerializer,
            403: OpenApiResponse(description="Not a manager of this event's team"),
        },
        description=(
            "Save this session as a reusable team template (managers only). Copies "
            "goal/total/sport and deep-copies the rounds; the template is independent "
            "of later edits to this event."
        ),
    )
    @action(detail=True, methods=["post"], url_path="save-as-template")
    def save_as_template(self, request, pk=None):
        """POST /events/{id}/save-as-template/ {name} — create an EventTemplate."""
        from .models import EventTemplate

        event = self.get_object()
        team = self._require_event_manager(event)

        body = SaveAsTemplateRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        with transaction.atomic():
            template = EventTemplate.objects.create(
                team=team,
                name=body.validated_data["name"],
                goal=event.goal or "",
                total=event.total,
                sport=event.sport,
                created_by=request.user if request.user.is_authenticated else None,
            )
            template.rounds.set(_clone_rounds(event.rounds.all()))

        return Response(EventTemplateSerializer(template).data, status=status.HTTP_201_CREATED)


class EventTemplateViewSet(viewsets.ModelViewSet):
    """Reusable session templates, scoped to teams the caller manages.

    URL: /api/v1/event-templates/
    - GET (list, optional ?team=): the caller's managed teams' templates.
    - DELETE /{id}/: remove a template (manager of its team).
    - POST /{id}/instantiate/ {refer_program, date}: create an Event from it.
    """

    serializer_class = EventTemplateSerializer
    http_method_names = ["get", "delete", "post", "head", "options"]
    # Declared so drf-spectacular exposes ?team to the generated client (the
    # frontend filters its team's templates with it).
    filterset_fields = ["team"]
    ordering = ["name"]

    def get_queryset(self):
        from django.db.models import Count

        from .models import EventTemplate

        if getattr(self, "swagger_fake_view", False):
            return EventTemplate.objects.none()
        # Annotate the rounds count so the list serializer avoids one COUNT/row.
        return (
            EventTemplate.objects.filter(team__in=managed_teams(self.request.user))
            .select_related("sport", "team")
            .annotate(_rounds_count=Count("rounds"))
        )

    @extend_schema(
        request=InstantiateTemplateRequestSerializer,
        responses={
            201: EventSerializer,
            400: OpenApiResponse(description="Invalid program/date or program not in template team"),
            403: OpenApiResponse(description="Not a manager of the template's team"),
        },
        description=(
            "Create a new Event from this template onto the given program + date "
            "(manager of the template's team only). Deep-copies the template's rounds."
        ),
    )
    @action(detail=True, methods=["post"])
    def instantiate(self, request, pk=None):
        from program.models import Program
        from tools.choices import TrainingType

        template = self.get_object()  # already scoped to managed teams
        body = InstantiateTemplateRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        program = (
            Program.objects.filter(pk=body.validated_data["refer_program"])
            .select_related("team")
            .first()
        )
        if program is None or program.team_id != template.team_id:
            raise PermissionDenied(_("The program must belong to the template's team."))

        with transaction.atomic():
            event = Event.objects.create(
                refer_program=program,
                name=template.name,
                goal=template.goal,
                total=template.total,
                sport=template.sport or program.team.default_sport,
                date=body.validated_data["date"],
                # Templates capture rounds, so the instantiated session is
                # structured (the create() above bypasses the serializer's
                # training-type seeding, so set it explicitly).
                training_type=TrainingType.STRUCTURED,
                vis_distance=program.team.vis_distance,
                vis_goal=program.team.vis_goal,
                vis_rounds=program.team.vis_rounds,
            )
            for r in _clone_rounds(template.rounds.all()):
                event.rounds.add(r)

        return Response(
            EventSerializer(event, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

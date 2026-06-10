from django.db.models import Count, F, OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from team.queries import managed_teams, user_member_teams
from tools.mixins import TeamScopedViewMixin

from .models import Message, Topic, TopicAudience, TopicRead
from .permissions import (
    IsTeamTopicVisibilityAndCoachWrite,
    IsTopicMessagePermission,
)
from .serializers import (
    MessageSerializer,
    TopicSerializer,
    UnreadSummarySerializer,
)

_TEAM_PK_PARAM = OpenApiParameter(
    name="team_pk",
    type=OpenApiTypes.INT,
    location=OpenApiParameter.PATH,
    description="ID of the parent team.",
)
_TOPIC_PK_PARAM = OpenApiParameter(
    name="topic_pk",
    type=OpenApiTypes.INT,
    location=OpenApiParameter.PATH,
    description="ID of the parent topic.",
)


def _topic_recipients(topic, *, exclude_user=None):
    """Return the set of users who can SEE ``topic`` and should be notified.

    - audience=team: all active athlete-members (with a linked user) + the
      team owner + managers.
    - audience=coaches: the team owner + managers only.

    The optional ``exclude_user`` (the actor) is removed; ``notify()`` also
    skips the actor defensively.
    """
    team = topic.team
    recipients = {team.owner}
    recipients.update(team.managers.all())

    if topic.audience == TopicAudience.TEAM:
        from customuser.models import CustomUser

        athlete_user_ids = team.memberships.filter(
            left_at__isnull=True, member__user_id__isnull=False
        ).values_list("member__user_id", flat=True)
        recipients.update(CustomUser.objects.filter(pk__in=set(athlete_user_ids)))

    recipients.discard(None)
    if exclude_user is not None:
        recipients.discard(exclude_user)
    return recipients


@extend_schema_view(
    list=extend_schema(
        summary="List visible topics for a team (newest activity first)",
        operation_id="teams_topics_list",
    ),
    retrieve=extend_schema(
        summary="Retrieve a topic",
        operation_id="teams_topics_retrieve",
    ),
    create=extend_schema(
        summary="Create a topic (coach only)",
        operation_id="teams_topics_create",
    ),
    destroy=extend_schema(
        summary="Delete a topic (author or coach)",
        operation_id="teams_topics_destroy",
    ),
)
class TopicViewSet(TeamScopedViewMixin, viewsets.ModelViewSet):
    """Topics nested under a team.

    URL: /api/v1/teams/{team_pk}/topics/
    """

    serializer_class = TopicSerializer
    permission_classes = [IsTeamTopicVisibilityAndCoachWrite]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_permissions(self):
        # Marking a topic read is allowed for any member who can SEE it (the
        # action's get_object() enforces visibility via get_queryset); it is not
        # a coach-only write of topic content.
        if getattr(self, "action", None) == "read":
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Topic.objects.none()

        team = self.get_team()
        if team is None:
            return Topic.objects.none()

        qs = (
            Topic.objects.filter(team=team)
            .select_related("author", "team")
            .annotate(message_count=Count("messages"))
            .order_by("-updated_at")
        )

        # Athletes (non-coaches) only ever see team-audience topics; coaches-
        # only topics must never leak. Coaches see everything.
        if not team.is_managed_by(self.request.user):
            qs = qs.filter(audience=TopicAudience.TEAM)

        return qs

    def perform_create(self, serializer):
        team = self.get_team()
        user = self.request.user

        # Audience constraint for non-coach creators: an athlete member may
        # only create a whole-team topic. Coaches (owner/managers) keep both
        # audiences. Permission-level (can_create_topic) already vetted that
        # the user may create at all.
        if not team.is_managed_by(user):
            requested = serializer.validated_data.get(
                "audience", TopicAudience.TEAM
            )
            if requested != TopicAudience.TEAM:
                raise PermissionDenied(
                    _("Athletes can only create whole-team topics.")
                )

        topic = serializer.save(team=team, author=user)
        self._notify_new_topic(topic)

    def _notify_new_topic(self, topic):
        """Notify the topic's audience (except the author) of a new topic."""
        from notifications.models import NotificationType
        from notifications.services import notify_many

        actor = self.request.user
        url = f"/teams/{topic.team_id}"
        recipients = _topic_recipients(topic, exclude_user=actor)
        notify_many(
            recipients,
            NotificationType.MESSAGE_NEW_TOPIC,
            title=_("New topic: %(title)s") % {"title": topic.title},
            body=_("A new topic was created in your team."),
            url=url,
            actor=actor,
        )

    @extend_schema(
        summary="Mark a topic as read up to now (per-user read state)",
        operation_id="teams_topics_read",
        parameters=[_TEAM_PK_PARAM],
        request=None,
        responses={204: None},
    )
    @action(detail=True, methods=["post"], url_path="read")
    def read(self, request, team_pk=None, pk=None):
        """POST /teams/{team}/topics/{id}/read/ — set the caller's last-read
        marker for this topic to now (so its messages count as read)."""
        topic = self.get_object()  # 404s if the user can't see the topic
        TopicRead.objects.update_or_create(
            user=request.user,
            topic=topic,
            defaults={"last_read_at": timezone.now()},
        )
        return Response(status=204)


@extend_schema_view(
    list=extend_schema(
        summary="List messages in a topic (oldest first)",
        operation_id="teams_topics_messages_list",
        parameters=[_TEAM_PK_PARAM, _TOPIC_PK_PARAM],
    ),
    retrieve=extend_schema(
        summary="Retrieve a message",
        operation_id="teams_topics_messages_retrieve",
        parameters=[_TEAM_PK_PARAM, _TOPIC_PK_PARAM],
    ),
    create=extend_schema(
        summary="Post a message in a topic",
        operation_id="teams_topics_messages_create",
        parameters=[_TEAM_PK_PARAM, _TOPIC_PK_PARAM],
    ),
    partial_update=extend_schema(
        summary="Edit a message (author only)",
        operation_id="teams_topics_messages_partial_update",
        parameters=[_TEAM_PK_PARAM, _TOPIC_PK_PARAM],
    ),
    update=extend_schema(
        summary="Edit a message (author only)",
        operation_id="teams_topics_messages_update",
        parameters=[_TEAM_PK_PARAM, _TOPIC_PK_PARAM],
    ),
    destroy=extend_schema(
        summary="Delete a message (author or coach)",
        operation_id="teams_topics_messages_destroy",
        parameters=[_TEAM_PK_PARAM, _TOPIC_PK_PARAM],
    ),
)
class TopicMessageViewSet(TeamScopedViewMixin, viewsets.ModelViewSet):
    """Messages nested under a topic.

    URL: /api/v1/teams/{team_pk}/topics/{topic_pk}/messages/
    """

    serializer_class = MessageSerializer
    permission_classes = [IsTopicMessagePermission]
    http_method_names = ["get", "post", "patch", "put", "delete", "head", "options"]

    def get_topic(self):
        if not hasattr(self, "_topic"):
            topic_pk = self.kwargs.get("topic_pk")
            team_pk = self.kwargs.get("team_pk")
            if not topic_pk or not team_pk:
                self._topic = None
            else:
                self._topic = get_object_or_404(Topic, pk=topic_pk, team_id=team_pk)
        return self._topic

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Message.objects.none()

        topic = self.get_topic()
        if topic is None:
            return Message.objects.none()

        return Message.objects.filter(topic=topic).select_related("author")

    def perform_create(self, serializer):
        topic = self.get_topic()
        message = serializer.save(topic=topic, author=self.request.user)
        # Bump topic activity so the topic list re-orders to most-recent.
        Topic.objects.filter(pk=topic.pk).update(updated_at=timezone.now())
        self._notify_new_message(topic, message)

    def perform_update(self, serializer):
        # Author-only (enforced by the permission); stamp edited_at.
        serializer.save(edited_at=timezone.now())

    def _notify_new_message(self, topic, message):
        """Notify everyone who can see the topic (except the sender)."""
        from notifications.models import NotificationType
        from notifications.services import notify_many

        actor = self.request.user
        url = f"/teams/{topic.team_id}"
        recipients = _topic_recipients(topic, exclude_user=actor)
        notify_many(
            recipients,
            NotificationType.MESSAGE_NEW_REPLY,
            title=_("New message in: %(title)s") % {"title": topic.title},
            body=_("A new message was posted in a topic you follow."),
            url=url,
            actor=actor,
        )


@extend_schema(
    summary="Unread discussion summary for the current user",
    operation_id="discussions_unread",
    responses={200: UnreadSummarySerializer},
)
class DiscussionsUnreadView(APIView):
    """GET /api/v1/discussions/unread/ — the caller's unread count + the topics
    that have unread messages, across all teams they are a member of.

    Unread for a topic = messages created after the user's last-read marker
    (TopicRead), excluding the user's own messages. No marker = all such
    messages are unread. Coaches-only topics are included only for managers.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        member_teams = user_member_teams(user)
        managed_ids = set(managed_teams(user).values_list("id", flat=True))

        topics = list(
            Topic.objects.filter(team__in=member_teams)
            .select_related("team")
            .order_by("-updated_at")
        )
        # Audience: coaches-only topics are visible to managers only.
        visible = [
            t for t in topics
            if t.audience == TopicAudience.TEAM or t.team_id in managed_ids
        ]

        visible_ids = [t.id for t in visible]
        # One grouped query for every topic's unread count (was one COUNT per
        # topic in a loop). Each message is compared against the caller's
        # per-topic last-read marker via a correlated subquery; a missing marker
        # (NULL) means everything counts.
        last_read_sq = TopicRead.objects.filter(
            user=user, topic=OuterRef("topic")
        ).values("last_read_at")[:1]
        counts = {
            row["topic"]: row["n"]
            for row in (
                Message.objects.filter(topic_id__in=visible_ids)
                .exclude(author=user)
                .annotate(_last_read=Subquery(last_read_sq))
                .filter(Q(_last_read__isnull=True) | Q(created_at__gt=F("_last_read")))
                .values("topic")
                .annotate(n=Count("id"))
            )
        }

        rows = []
        total = 0
        for t in visible:
            n = counts.get(t.id, 0)
            if n:
                total += n
                rows.append(
                    {
                        "topic_id": t.id,
                        "team_id": t.team_id,
                        "team_name": t.team.name,
                        "title": t.title,
                        "unread_count": n,
                        "updated_at": t.updated_at,
                    }
                )

        return Response(UnreadSummarySerializer({"count": total, "topics": rows}).data)

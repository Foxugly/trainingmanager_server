from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied

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

from team.models import Team

from .models import Message, Topic, TopicAudience
from .permissions import (
    IsTeamTopicVisibilityAndCoachWrite,
    IsTopicMessagePermission,
)
from .serializers import MessageSerializer, TopicSerializer


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
class TopicViewSet(viewsets.ModelViewSet):
    """Topics nested under a team.

    URL: /api/v1/teams/{team_pk}/topics/
    """

    serializer_class = TopicSerializer
    permission_classes = [IsTeamTopicVisibilityAndCoachWrite]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_team(self):
        team_pk = self.kwargs.get("team_pk")
        if not team_pk:
            return None
        return get_object_or_404(Team, pk=team_pk)

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
        from notifications.services import notify

        actor = self.request.user
        url = f"/teams/{topic.team_id}"
        recipients = _topic_recipients(topic, exclude_user=actor)
        for recipient in recipients:
            notify(
                recipient,
                NotificationType.MESSAGE_NEW_TOPIC,
                title=_("New topic: %(title)s") % {"title": topic.title},
                body=_("A new topic was created in your team."),
                url=url,
                actor=actor,
            )


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
    destroy=extend_schema(
        summary="Delete a message (author or coach)",
        operation_id="teams_topics_messages_destroy",
        parameters=[_TEAM_PK_PARAM, _TOPIC_PK_PARAM],
    ),
)
class TopicMessageViewSet(viewsets.ModelViewSet):
    """Messages nested under a topic.

    URL: /api/v1/teams/{team_pk}/topics/{topic_pk}/messages/
    """

    serializer_class = MessageSerializer
    permission_classes = [IsTopicMessagePermission]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_team(self):
        team_pk = self.kwargs.get("team_pk")
        if not team_pk:
            return None
        return get_object_or_404(Team, pk=team_pk)

    def get_topic(self):
        topic_pk = self.kwargs.get("topic_pk")
        team_pk = self.kwargs.get("team_pk")
        if not topic_pk or not team_pk:
            return None
        return get_object_or_404(Topic, pk=topic_pk, team_id=team_pk)

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

    def _notify_new_message(self, topic, message):
        """Notify everyone who can see the topic (except the sender)."""
        from notifications.models import NotificationType
        from notifications.services import notify

        actor = self.request.user
        url = f"/teams/{topic.team_id}"
        recipients = _topic_recipients(topic, exclude_user=actor)
        for recipient in recipients:
            notify(
                recipient,
                NotificationType.MESSAGE_NEW_REPLY,
                title=_("New message in: %(title)s") % {"title": topic.title},
                body=_("A new message was posted in a topic you follow."),
                url=url,
                actor=actor,
            )

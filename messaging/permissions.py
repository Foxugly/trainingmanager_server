from django.utils.translation import gettext_lazy as _
from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import TopicAudience


def is_athlete_member(team, user):
    """True iff ``user`` is an active athlete member of ``team`` (linked via
    Member.user with a membership that has not been left)."""
    return team.memberships.filter(
        member__user_id=user.pk, left_at__isnull=True
    ).exists()


def can_see_topic(topic, user):
    """A user can SEE a topic iff they are a coach (owner/manager) of the
    team — in which case they see every topic — OR they are an active
    athlete member AND the topic audience is ``team``.

    Coaches-only topics are never visible to athletes.
    """
    team = topic.team
    if team.is_managed_by(user):
        return True
    if topic.audience == TopicAudience.TEAM:
        return is_athlete_member(team, user)
    return False


class IsTeamTopicVisibilityAndCoachWrite(BasePermission):
    """Permission for team-nested topics.

    has_permission (list/create):
      - any authenticated team participant (coach or active athlete member)
        may reach list/retrieve; the queryset filters out topics they cannot
        see.
      - create: owner/managers only (athletes get 403).

    has_object_permission:
      - read: only if the user can SEE the topic.
      - delete: the topic author OR a team owner/manager.

    The view must implement get_team() to resolve the URL context.
    """

    message = _("You don't have permission to access this team's topics.")

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        team = view.get_team()
        if team is None:
            return False

        user = request.user
        is_coach = team.is_managed_by(user)
        is_member = is_coach or is_athlete_member(team, user)
        if not is_member:
            return False

        # CREATE topic: coaches only.
        if getattr(view, "action", None) == "create":
            return is_coach

        # list / retrieve / destroy reach object-level / queryset filtering.
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return can_see_topic(obj, request.user)

        # DELETE: author or team owner/manager.
        team = obj.team
        if team.is_managed_by(request.user):
            return True
        return obj.author_id == request.user.pk


class IsTopicMessagePermission(BasePermission):
    """Permission for messages nested under a topic.

    has_permission (list/create):
      - the user must be able to SEE the parent topic at all.
      - create: owner/managers always; athlete members only when the topic
        audience is ``team`` AND ``allow_athlete_replies`` is True (else 403).

    has_object_permission:
      - read: the user can see the parent topic.
      - delete: the message author OR a team owner/manager.

    The view must implement get_team() and get_topic() to resolve context.
    """

    message = _("You don't have permission to post in this topic.")

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        topic = view.get_topic()
        if topic is None:
            return False

        user = request.user
        if not can_see_topic(topic, user):
            return False

        # POST a message.
        if getattr(view, "action", None) == "create":
            team = topic.team
            if team.is_managed_by(user):
                return True
            # Athlete member: only when audience=team AND replies allowed.
            return topic.audience == TopicAudience.TEAM and topic.allow_athlete_replies

        return True

    def has_object_permission(self, request, view, obj):
        topic = obj.topic
        if request.method in SAFE_METHODS:
            return can_see_topic(topic, request.user)

        # DELETE: author or team owner/manager.
        if topic.team.is_managed_by(request.user):
            return True
        return obj.author_id == request.user.pk

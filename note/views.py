from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.exceptions import PermissionDenied

from member.models import Member
from team.models import Team
from tools.mixins import SoftDeleteIncludeInactiveModelViewSet
from tools.openapi import INCLUDE_INACTIVE_PARAM

from .models import Note
from .permissions import IsTeamCoachOrReadOwnNotes
from .serializers import NoteSerializer


@extend_schema_view(
    list=extend_schema(
        summary="List coach notes for a member in a team",
        parameters=[INCLUDE_INACTIVE_PARAM],
    ),
    retrieve=extend_schema(parameters=[INCLUDE_INACTIVE_PARAM]),
    update=extend_schema(parameters=[INCLUDE_INACTIVE_PARAM]),
    partial_update=extend_schema(parameters=[INCLUDE_INACTIVE_PARAM]),
)
class NoteViewSet(SoftDeleteIncludeInactiveModelViewSet):
    """CRUD on coach notes within a team-member nested context.

    URL: /api/v1/teams/{team_pk}/members/{member_pk}/notes/
    """

    serializer_class = NoteSerializer
    permission_classes = [IsTeamCoachOrReadOwnNotes]
    soft_delete_fields = ("is_active", "updated_at")

    def get_team(self):
        # Memoized per request: permission check + get_queryset call this for
        # the same URL pk. ``None`` is a valid result, so use hasattr as the
        # "resolved" sentinel.
        if not hasattr(self, "_team"):
            team_pk = self.kwargs.get("team_pk")
            self._team = get_object_or_404(Team, pk=team_pk) if team_pk else None
        return self._team

    def get_member_or_none(self):
        if not hasattr(self, "_member"):
            member_pk = self.kwargs.get("member_pk")
            self._member = get_object_or_404(Member, pk=member_pk) if member_pk else None
        return self._member

    def _include_inactive_allowed(self, request):
        # Notes are team-scoped: managers (not staff) decide who sees the
        # archive. Override the default staff-only policy from the mixin.
        if request.query_params.get("include_inactive") != "true":
            return False
        team = self.get_team()
        return team is not None and team.is_managed_by(request.user)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Note.objects.none()

        team = self.get_team()
        member = self.get_member_or_none()
        if team is None or member is None:
            return Note.objects.none()

        qs = Note.objects.filter(team=team, member=member).select_related(
            "author", "team", "member"
        )

        # Athletes (non-managers reading their own member's notes) only ever
        # see notes explicitly shared with them AND still active — regardless
        # of the include_inactive flag. Coaches go through the normal
        # active/include_inactive filter.
        if not team.is_managed_by(self.request.user):
            return qs.filter(visible_to_athlete=True, is_active=True)

        return self._apply_include_inactive_filter(qs)

    def perform_create(self, serializer):
        team = self.get_team()
        member = self.get_member_or_none()
        if not member.memberships.filter(team=team, left_at__isnull=True).exists():
            raise PermissionDenied(_("This member does not belong to this team."))
        note = serializer.save(team=team, member=member, author=self.request.user)
        self._notify_on_create(note, team, member)

    def _notify_on_create(self, note, team, member):
        """Fan out in-app + email notifications after a note is created.

        - The concerned athlete (if linked to a user) is notified when the
          team toggle is on AND the note is shared with them.
        - The team's owner + managers (except the note's author) are notified
          when the team toggle is on.

        Translatable strings are passed lazily; ``notify()`` resolves them per
        recipient under ``translation.override(recipient.language)``.
        """
        from notifications.models import NotificationType
        from notifications.services import notify, notify_many

        actor = self.request.user
        member_name = member.get_fullname()
        url = f"/teams/{team.id}"

        # Athlete: only when shared and the member has a linked user.
        if team.notify_athlete_on_visible_note and note.visible_to_athlete and member.user_id:
            notify(
                member.user,
                NotificationType.NOTE_FOR_ATHLETE,
                title=_("A note was shared with you"),
                body=_("Your coach added a note for you."),
                url=url,
                actor=actor,
            )

        # Coaches: owner + managers, excluding the author.
        if team.notify_coaches_on_note:
            coaches = {team.owner}
            coaches.update(team.managers.all())
            notify_many(
                coaches,
                NotificationType.NOTE_FOR_COACH,
                title=_("Note added on %(name)s") % {"name": member_name},
                body=_("A new note was added on %(name)s.") % {"name": member_name},
                url=url,
                actor=actor,
            )

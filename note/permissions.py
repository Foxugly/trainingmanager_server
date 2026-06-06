from django.utils.translation import gettext_lazy as _
from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsTeamCoachOrReadOwnNotes(BasePermission):
    """Permission for team-scoped notes.

    Read (SAFE_METHODS):
      - team owner / managers always (they see every note of their team)
      - the concerned athlete (the note's member belongs to the requesting
        user) may read their own member's notes. Per-note visibility is
        enforced by the view queryset (visible_to_athlete=True AND
        is_active=True); this permission only gates that the athlete is
        accessing their OWN member record.

    Write (POST/PATCH/PUT/DELETE):
      - team owner / managers only

    The view must implement get_team() and get_member_or_none() so
    this permission can resolve the URL context.
    """

    message = _("You don't have permission to access these notes.")

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        team = view.get_team()
        member = view.get_member_or_none()

        if team is None or member is None:
            return False

        if team.is_managed_by(request.user):
            return True

        if request.method in SAFE_METHODS:
            # The athlete may only access notes attached to their own member
            # record; the queryset further restricts to visible+active notes.
            return member.user_id == request.user.pk

        return False

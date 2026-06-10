from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import serializers as drf_serializers
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated

from tools.exceptions import NotAuthorizedMemberDenied
from tools.mixins import TeamScopedViewMixin
from tools.openapi import INCLUDE_INACTIVE_PARAM

from ..models import TeamMembership, TrainingSlot
from ..queries import managed_teams, user_member_teams
from ..serializers import TeamMembershipSerializer, TrainingSlotSerializer


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
            from audit.services import audit_event

            member = instance.member
            audit_event(
                self.request,
                AuditAction.MEMBER_REMOVED,
                team=team,
                target_repr=f"Member #{member.id} ({member.get_fullname()})",
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

from django.db import transaction
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response

from team.queries import managed_teams, user_member_teams

from .models import Member
from .serializers import MemberSerializer


class MemberViewSet(viewsets.ModelViewSet):
    """CRUD complet pour Member, scopé par teams du Member."""

    serializer_class = MemberSerializer
    filterset_fields = ["lastname", "firstname"]
    search_fields = ["firstname", "lastname", "email"]
    ordering_fields = ["lastname", "firstname", "id"]
    ordering = ["lastname", "firstname"]

    def get_queryset(self):
        return (
            Member.objects.filter(
                memberships__team__in=user_member_teams(self.request.user),
                memberships__left_at__isnull=True,
            )
            .select_related("user")
            .prefetch_related("memberships")
            .distinct()
        )

    def _check_user_manages_a_team(self):
        if not managed_teams(self.request.user).exists():
            raise PermissionDenied(_("You must manage at least one team to create members."))

    def _require_manages_member_team(self, member):
        """The caller must manage (own/manage) a team this member belongs to.

        ``get_queryset`` only scopes to teams the caller is a MEMBER of (which
        includes teams where they are a mere athlete), so without this check a
        user could edit/delete members of a team they don't manage. Mirrors the
        anonymize permission.
        """
        coach_team_ids = set(managed_teams(self.request.user).values_list("pk", flat=True))
        member_team_ids = set(member.memberships.values_list("team_id", flat=True))
        if not (coach_team_ids & member_team_ids):
            raise PermissionDenied(_("You must manage a team this member belongs to."))

    def perform_create(self, serializer):
        self._check_user_manages_a_team()
        serializer.save()

    def perform_update(self, serializer):
        self._require_manages_member_team(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._require_manages_member_team(instance)
        instance.delete()

    @extend_schema(
        operation_id="members_anonymize",
        request=None,
        responses={
            200: OpenApiResponse(
                response=MemberSerializer,
                description=(
                    "PII irreversibly blanked. Returns the anonymized member."
                ),
            ),
            403: OpenApiResponse(
                description=(
                    "The caller is not a coach (owner/manager) of any team this "
                    "member belongs to."
                )
            ),
            404: OpenApiResponse(description="No such member in a team you manage."),
        },
    )
    @action(detail=True, methods=["post"])
    def anonymize(self, request, pk=None):
        """Irreversibly anonymize an athlete's personal data (RGPD erasure).

        A coach (owner/manager) of a team the member belongs to (active OR
        past membership) blanks the member's PII: firstname/lastname become a
        neutral placeholder, email and phonenumber are cleared. Any linked
        user account is UNLINKED (member.user = None) but NOT deleted — users
        own their own account. Coach notes ABOUT this member are deleted (they
        may contain PII). Training history (memberships, performances, rsvp,
        roti) is KEPT, now tied to the anonymized member so aggregate stats
        survive.

        THIS IS IRREVERSIBLE. There is no undo.
        """
        member, audit_team = self._get_member_for_anonymize(request, pk)

        with transaction.atomic():
            # Delete coach notes about this member first (may contain PII).
            try:
                member.notes.all().delete()
            except Exception:  # pragma: no cover - note app may be absent
                pass

            member.firstname = "Athlète"
            member.lastname = f"anonymisé #{member.id}"
            member.email = ""
            member.phonenumber = ""
            # Unlink any user account without touching/deleting it.
            member.user = None
            member.save(
                update_fields=[
                    "firstname",
                    "lastname",
                    "email",
                    "phonenumber",
                    "user",
                    "updated_at",
                ]
            )

            # Audit the erasure (best-effort; never breaks the action).
            from audit.models import AuditAction
            from audit.services import record

            record(
                AuditAction.MEMBER_ANONYMIZED,
                actor=request.user,
                team=audit_team,
                target_repr=f"Member #{member.id}",
                request=request,
            )

        member.refresh_from_db()
        serializer = self.get_serializer(member)
        return Response(serializer.data)

    def _get_member_for_anonymize(self, request, pk):
        """Resolve a Member the caller is allowed to anonymize, and the team.

        The caller must be a coach (owner/manager) of a team the member has a
        TeamMembership in (active OR past). A member with no membership in any
        team the caller manages is indistinguishable from a non-existent one
        for this caller -> 404.

        Returns a ``(member, team)`` tuple where ``team`` is one managed team
        the member belongs to (used to scope the audit log entry).
        """
        try:
            member = Member.objects.get(pk=pk)
        except Member.DoesNotExist:
            raise NotFound(_("No such member."))

        coach_team_ids = set(
            managed_teams(request.user).values_list("pk", flat=True)
        )
        member_team_ids = set(
            member.memberships.values_list("team_id", flat=True)
        )
        shared_team_ids = coach_team_ids & member_team_ids
        if not shared_team_ids:
            raise NotFound(_("No such member in a team you manage."))

        from team.models import Team

        audit_team = Team.objects.filter(pk__in=shared_team_ids).first()
        return member, audit_team

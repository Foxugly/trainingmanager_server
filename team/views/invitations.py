import logging

from django.conf import settings as dj_settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone, translation
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import TeamInvitation
from ..permissions import IsTrainer
from ..queries import managed_teams
from ..serializers import (
    CompleteInvitationSerializer,
    CreateInvitationSerializer,
    TeamInvitationSerializer,
    ValidateInvitationSerializer,
)

logger = logging.getLogger(__name__)


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
                f"{invitation.invited_by.get_full_name() or invitation.invited_by.email} "
                f"{_('has invited you to join the team')} "
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
                        "email": drf_serializers.EmailField(),
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
            409: OpenApiResponse(
                description=(
                    "An account already exists for the invitation email "
                    "(code=email_taken)."
                )
            ),
        },
        description="Finalize invitation: create the user, link Member, return JWT.",
    )
    def post(self, request, token):
        from django.db import IntegrityError
        from rest_framework_simplejwt.tokens import RefreshToken

        from ..models import TeamMembership

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

        # Re-validate that the invitation email is not already claimed before
        # create_user — otherwise the unique-email constraint raises
        # IntegrityError and surfaces as a 500. Return a clean 409
        # (code=email_taken) instead. This can legitimately happen if a user
        # registered with this email between the invitation being sent and
        # finalized (the create-invitation path only refuses existing emails at
        # send time).
        if User.objects.filter(email__iexact=invitation.email).exists():
            return Response(
                {
                    "code": "email_taken",
                    "detail": _(
                        "An account already exists for this email address. "
                        "Please log in instead."
                    ),
                },
                status=status.HTTP_409_CONFLICT,
            )

        with transaction.atomic():
            # email_confirmed=True: the invitee proved control of this mailbox
            # by following the tokenized invitation link sent to it (the same
            # ownership proof allauth's verified EmailAddress used to encode).
            user = User.objects.create_user(
                email=invitation.email,
                password=serializer.validated_data["password"],
                first_name=invitation.member.firstname,
                last_name=invitation.member.lastname,
                is_active=True,
                email_confirmed=True,
            )
            invitation.member.user = user
            invitation.member.save()

            # Add the now-registered athlete to the team — the whole point of
            # the invitation. The TeamMembership post_save signal then attaches
            # them to the team's future events. Guard the active-membership
            # unique constraint with a savepoint (idempotent if somehow already
            # a member).
            if not TeamMembership.objects.filter(
                team=invitation.team, member=invitation.member, left_at__isnull=True
            ).exists():
                try:
                    with transaction.atomic():
                        TeamMembership.objects.create(
                            team=invitation.team, member=invitation.member
                        )
                except IntegrityError:
                    pass

            invitation.status = "completed"
            invitation.completed_at = timezone.now()
            invitation.save()

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "detail": _("Account created and invitation finalized."),
                "email": user.email,
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            },
            status=status.HTTP_201_CREATED,
        )

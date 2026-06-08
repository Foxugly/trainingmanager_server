from django.urls import path
from rest_framework.routers import DefaultRouter
from rest_framework_nested.routers import NestedSimpleRouter

from .views import (
    InvitationLookupView,
    TeamInvitationViewSet,
    TeamJoinRequestMagicActionExecuteView,
    TeamJoinRequestMagicActionPreviewView,
    TeamJoinRequestViewSet,
    TeamMembershipViewSet,
    TeamViewSet,
    TrainingSlotViewSet,
)

router = DefaultRouter()
router.register(r"teams", TeamViewSet, basename="team")
router.register(r"join-requests", TeamJoinRequestViewSet, basename="joinrequest")
router.register(r"invitations", TeamInvitationViewSet, basename="invitation")

memberships_router = NestedSimpleRouter(router, r"teams", lookup="team")
memberships_router.register(r"memberships", TeamMembershipViewSet, basename="team-membership")
memberships_router.register(
    r"training-slots", TrainingSlotViewSet, basename="team-training-slot"
)

urlpatterns = (
    router.urls
    + memberships_router.urls
    + [
        path(
            "invitations/lookup/<str:token>/",
            InvitationLookupView.as_view(),
            name="invitation-lookup",
        ),
        # Magic-action: GET preview by token / POST {token} executes.
        # Distinct base path to avoid collision with the DRF router's
        # retrieve regex on /join-requests/<pk>/.
        # Two distinct views (instead of one with GET+POST on both URLs)
        # so drf-spectacular emits unique operationIds.
        path(
            "join-magic/<str:token>/",
            TeamJoinRequestMagicActionPreviewView.as_view(),
            name="joinrequest-magic-action-preview",
        ),
        path(
            "join-magic/",
            TeamJoinRequestMagicActionExecuteView.as_view(),
            name="joinrequest-magic-action-execute",
        ),
    ]
)

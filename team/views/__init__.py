"""Team views, split by concern:

- ``teams``        — TeamViewSet (CRUD + stats / review-block / pools /
                     training-template / roster-history / rsvp-reliability /
                     roti-drift) and its response serializer.
- ``join_requests``— self-signup join-request flow + the magic-action views.
- ``invitations``  — trainer invitation flow + public invitation lookup.
- ``memberships``  — team membership + weekly training-slot CRUD.

Re-exported flat so ``from team.views import X`` keeps working for urls.py
and the rest of the codebase.

``translation`` is re-exported here on purpose: ``tests/test_email_i18n.py``
patches ``team.views.translation.override`` to assert the join-request mail is
rendered in each recipient's language. The submodules import the same
``django.utils.translation`` module object, so patching it here patches it
everywhere it is used.
"""

from django.utils import translation  # noqa: F401  (re-exported for test monkeypatch)

from .invitations import InvitationLookupView, TeamInvitationViewSet
from .join_requests import (
    TeamJoinRequestMagicActionExecuteView,
    TeamJoinRequestMagicActionPreviewView,
    TeamJoinRequestViewSet,
)
from .memberships import TeamMembershipViewSet, TrainingSlotViewSet
from .teams import TeamPoolsResponseSerializer, TeamViewSet

__all__ = [
    "InvitationLookupView",
    "TeamInvitationViewSet",
    "TeamJoinRequestMagicActionExecuteView",
    "TeamJoinRequestMagicActionPreviewView",
    "TeamJoinRequestViewSet",
    "TeamMembershipViewSet",
    "TeamPoolsResponseSerializer",
    "TeamViewSet",
    "TrainingSlotViewSet",
]

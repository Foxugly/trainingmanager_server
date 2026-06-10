"""Team serializers, split by concern:

- ``core``        — Team CRUD + nested/minimal team serializers, membership,
                    training-slot / training-template, the team-sport read/write
                    helpers, and the logo validation constants
                    (``LOGO_DATA_URL_RE`` / ``LOGO_MAX_LENGTH``).
- ``stats``       — the ``Stats*`` aggregation serializers, ``StatsPeriod``, and
                    the windowed response wrappers (roster-history /
                    rsvp-reliability / roti-drift entry + response shapes).
- ``invitations`` — invitation, join-request, and magic-action serializers.
- ``ai_review``   — the ``Review*`` serializers for the AI training-block review.

Re-exported flat so ``from team.serializers import X`` keeps working for urls.py,
the views package, and the rest of the codebase (e.g. ``program.serializers``).
"""

from .ai_review import (
    ReviewAdjustmentSerializer,
    ReviewBlockRequestSerializer,
    ReviewBlockResponseSerializer,
    ReviewFindingSerializer,
)
from .core import (
    LOGO_DATA_URL_RE,
    LOGO_MAX_LENGTH,
    TeamMembershipSerializer,
    TeamMinimalSerializer,
    TeamSerializer,
    TeamSportReadSerializer,
    TrainingSlotSerializer,
    TrainingTemplateSerializer,
)
from .core import _SportTrainingTypeWriteSerializer as _SportTrainingTypeWriteSerializer
from .invitations import (
    CompleteInvitationSerializer,
    CreateInvitationSerializer,
    CreateJoinRequestSerializer,
    JoinMagicCancelledResponseSerializer,
    JoinMagicErrorSerializer,
    TeamInvitationSerializer,
    TeamJoinRequestMagicActionPostSerializer,
    TeamJoinRequestMagicActionResponseSerializer,
    TeamJoinRequestSerializer,
    ValidateInvitationSerializer,
)
from .invitations import _MagicActionJoinRequestSerializer as _MagicActionJoinRequestSerializer
from .stats import (
    RosterHistoryEntrySerializer,
    RosterHistoryResponseSerializer,
    RotiDriftEntrySerializer,
    RotiDriftResponseSerializer,
    RsvpReliabilityEntrySerializer,
    RsvpReliabilityResponseSerializer,
    StatsAttendanceByMemberSerializer,
    StatsAttendanceBySessionSerializer,
    StatsAttendanceSerializer,
    StatsIntensityBySegmentSerializer,
    StatsIntensitySerializer,
    StatsMemberSerializer,
    StatsPeriodSerializer,
    StatsRotiPointSerializer,
    StatsRotiSerializer,
    StatsVolumeByMemberSerializer,
    StatsVolumeByWeekSerializer,
    StatsVolumeSerializer,
    TeamStatsSerializer,
)

__all__ = [
    "LOGO_DATA_URL_RE",
    "LOGO_MAX_LENGTH",
    "CompleteInvitationSerializer",
    "CreateInvitationSerializer",
    "CreateJoinRequestSerializer",
    "JoinMagicCancelledResponseSerializer",
    "JoinMagicErrorSerializer",
    "ReviewAdjustmentSerializer",
    "ReviewBlockRequestSerializer",
    "ReviewBlockResponseSerializer",
    "ReviewFindingSerializer",
    "RosterHistoryEntrySerializer",
    "RosterHistoryResponseSerializer",
    "RotiDriftEntrySerializer",
    "RotiDriftResponseSerializer",
    "RsvpReliabilityEntrySerializer",
    "RsvpReliabilityResponseSerializer",
    "StatsAttendanceByMemberSerializer",
    "StatsAttendanceBySessionSerializer",
    "StatsAttendanceSerializer",
    "StatsIntensityBySegmentSerializer",
    "StatsIntensitySerializer",
    "StatsMemberSerializer",
    "StatsPeriodSerializer",
    "StatsRotiPointSerializer",
    "StatsRotiSerializer",
    "StatsVolumeByMemberSerializer",
    "StatsVolumeByWeekSerializer",
    "StatsVolumeSerializer",
    "TeamInvitationSerializer",
    "TeamJoinRequestMagicActionPostSerializer",
    "TeamJoinRequestMagicActionResponseSerializer",
    "TeamJoinRequestSerializer",
    "TeamMembershipSerializer",
    "TeamMinimalSerializer",
    "TeamSerializer",
    "TeamSportReadSerializer",
    "TeamStatsSerializer",
    "TrainingSlotSerializer",
    "TrainingTemplateSerializer",
    "ValidateInvitationSerializer",
]

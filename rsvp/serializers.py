from rest_framework import serializers

from .models import RsvpStatus


class RsvpUpsertSerializer(serializers.Serializer):
    """Request body for PUT .../rsvp/ — only the caller's own status."""

    status = serializers.ChoiceField(choices=RsvpStatus.choices)


class RsvpCountsSerializer(serializers.Serializer):
    """Per-status tally for an event's RSVPs, plus members who didn't reply."""

    going = serializers.IntegerField()
    maybe = serializers.IntegerField()
    not_going = serializers.IntegerField()
    no_response = serializers.IntegerField()


class RsvpByMemberSerializer(serializers.Serializer):
    """One row of the manager-facing per-member RSVP breakdown."""

    member_id = serializers.IntegerField()
    name = serializers.CharField()
    status = serializers.ChoiceField(choices=RsvpStatus.choices, allow_null=True)


class RsvpSummarySerializer(serializers.Serializer):
    """Aggregate availability for an event, plus the caller's own status.

    `by_member` is only populated for team managers (coach view); athletes
    get at least `my_status` (and the aggregate `counts`).
    """

    counts = RsvpCountsSerializer()
    total_members = serializers.IntegerField()
    my_status = serializers.ChoiceField(choices=RsvpStatus.choices, allow_null=True)
    by_member = RsvpByMemberSerializer(many=True)


class RsvpApplyToAttendanceResultSerializer(serializers.Serializer):
    """200 response for POST .../rsvp/apply_to_attendance/."""

    applied = serializers.IntegerField(
        help_text="Number of Attendance rows created or updated from RSVPs."
    )
    skipped = serializers.IntegerField(
        help_text="RSVPs left untouched (MAYBE, or a status with no mapped AttendanceStatus)."
    )

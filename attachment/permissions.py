"""Target resolution + read/write permission checks for attachments.

This module is the SINGLE source of truth for "who may attach to / read / delete
files on a given target". It deliberately REUSES the existing per-app rules:

  - Events: read = the user is a member of the event's team
    (team.queries.user_member_teams); write = the user manages that team
    (Team.is_managed_by).
  - Messages: read = the user can SEE the parent topic
    (messaging.permissions.can_see_topic); write = the message author OR a
    coach (owner/manager) of the topic's team — mirroring the messaging
    message-delete rule.

Only the labels "event" and "message" are accepted; anything else raises a
DRF ValidationError (-> HTTP 400) so callers fail loudly.
"""

from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import ValidationError

# The accepted target_type values, mapped to (app_label, model) for the
# ContentType lookup the model needs.
TARGET_MODELS = {
    "event": ("event", "event"),
    "message": ("messaging", "message"),
}


def content_type_for_label(label: str):
    """Return the ContentType for an accepted target label (or 400)."""
    from django.contrib.contenttypes.models import ContentType

    if label not in TARGET_MODELS:
        raise ValidationError(
            {"code": "invalid_target_type", "detail": _("Unsupported target_type.")}
        )
    app_label, model = TARGET_MODELS[label]
    return ContentType.objects.get(app_label=app_label, model=model)


def label_for_instance(instance) -> str | None:
    """Reverse map a model instance to its target label, or None if unsupported."""
    meta = instance._meta
    for label, (app_label, model) in TARGET_MODELS.items():
        if meta.app_label == app_label and meta.model_name == model:
            return label
    return None


def resolve_target(content_type_label: str, object_id):
    """Resolve (label, id) to the target model instance, or None if absent.

    Raises ValidationError (400) for an unsupported label.
    """
    if content_type_label not in TARGET_MODELS:
        raise ValidationError(
            {"code": "invalid_target_type", "detail": _("Unsupported target_type.")}
        )
    if content_type_label == "event":
        from event.models import Event

        return Event.objects.filter(pk=object_id).first()
    if content_type_label == "message":
        from messaging.models import Message

        return Message.objects.filter(pk=object_id).first()
    return None


def can_read_target(user, target) -> bool:
    """Whether ``user`` may read/list/download attachments on ``target``."""
    if target is None or not getattr(user, "is_authenticated", False):
        return False

    label = label_for_instance(target)
    if label == "event":
        from team.queries import user_member_teams

        program = target.refer_program
        if program is None:
            return False
        return user_member_teams(user).filter(pk=program.team_id).exists()

    if label == "message":
        from messaging.permissions import can_see_topic

        return can_see_topic(target.topic, user)

    return False


def can_write_target(user, target) -> bool:
    """Whether ``user`` may attach/delete files on ``target``."""
    if target is None or not getattr(user, "is_authenticated", False):
        return False

    label = label_for_instance(target)
    if label == "event":
        program = target.refer_program
        if program is None:
            return False
        return program.team.is_managed_by(user)

    if label == "message":
        # Author may attach to their own message; coaches (owner/managers) of
        # the topic's team may attach to any message — same rule the messaging
        # app uses for message deletion.
        if target.topic.team.is_managed_by(user):
            return True
        return target.author_id == user.pk

    return False

from django.db import models
from django.utils.translation import gettext_lazy as _


class TrainingType(models.TextChoices):
    """The kind of training content an Event carries (mutually exclusive).

    STRUCTURED — rounds + exercises (the swimming model).
    FREEFORM   — a single sanitized rich-text HTML blob.
    Add a new value here + a content field on Event + a branch to extend.
    """

    STRUCTURED = "structured", _("Structured (rounds & exercises)")
    FREEFORM = "freeform", _("Free text")

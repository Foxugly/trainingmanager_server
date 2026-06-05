from modeltranslation.translator import TranslationOptions, register

from .models import Level


@register(Level)
class LevelTranslationOptions(TranslationOptions):
    fields = ("name", "description")

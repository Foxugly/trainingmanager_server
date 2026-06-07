from modeltranslation.translator import TranslationOptions, register

from .models import Equipment


@register(Equipment)
class EquipmentTranslationOptions(TranslationOptions):
    fields = ("name",)

from django.contrib import admin
from modeltranslation.admin import TranslationAdmin

from .models import Level


@admin.register(Level)
class LevelAdmin(TranslationAdmin):
    list_display = ["code", "name", "order", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["code", "name"]
    ordering = ["order", "code"]
    fields = ["code", "name", "description", "order", "is_active"]

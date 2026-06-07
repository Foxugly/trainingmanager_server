from django.contrib import admin

from .models import Equipment


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "created_at")
    list_filter = ("team",)
    search_fields = ("name",)

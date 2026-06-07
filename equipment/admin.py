from django.contrib import admin

from .models import Equipment


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ("name", "sport", "is_active", "created_at")
    list_filter = ("sport", "is_active")
    search_fields = ("name",)

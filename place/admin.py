from django.contrib import admin

from .models import Place


@admin.register(Place)
class PlaceAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "address", "created_at")
    list_filter = ("team",)
    search_fields = ("name", "address")

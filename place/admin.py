from django.contrib import admin

from .models import Place


@admin.register(Place)
class PlaceAdmin(admin.ModelAdmin):
    list_display = ("name", "sport", "address", "created_at")
    list_filter = ("sport",)
    search_fields = ("name", "address")

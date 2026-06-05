from django.contrib import admin

from .models import Roti


@admin.register(Roti)
class RotiAdmin(admin.ModelAdmin):
    list_display = ["event", "member", "score", "created_at", "updated_at"]
    list_filter = ["score", "created_at"]
    search_fields = ["event__name", "member__firstname", "member__lastname"]
    readonly_fields = ["created_at", "updated_at"]
    raw_id_fields = ["event", "member"]

from django.contrib import admin

from .models import Rsvp


@admin.register(Rsvp)
class RsvpAdmin(admin.ModelAdmin):
    list_display = ["event", "member", "status", "created_at", "updated_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["event__name", "member__firstname", "member__lastname"]
    readonly_fields = ["created_at", "updated_at"]
    raw_id_fields = ["event", "member"]

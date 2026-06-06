from django.contrib import admin

from .models import Message, Topic


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ["id", "title", "team", "author", "audience", "allow_athlete_replies", "updated_at"]
    list_filter = ["audience", "allow_athlete_replies", "created_at"]
    search_fields = ["title", "team__name", "author__username"]
    raw_id_fields = ["team", "author"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ["id", "topic", "author", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["content", "topic__title", "author__username"]
    raw_id_fields = ["topic", "author"]
    readonly_fields = ["created_at"]

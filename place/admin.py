from django.contrib import admin

from .models import Place


@admin.register(Place)
class PlaceAdmin(admin.ModelAdmin):
    list_display = ("name", "sports_list", "address", "created_at")
    list_filter = ("sports",)
    search_fields = ("name", "address")

    @admin.display(description="Sports")
    def sports_list(self, obj):
        return ", ".join(s.name for s in obj.sports.all())

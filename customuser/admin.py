from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.utils.translation import gettext_lazy as _

from customuser.forms import CustomUserChangeForm, CustomUserCreationForm
from customuser.models import CustomUser


class CustomUserAdmin(UserAdmin):
    """Email-only UserAdmin. Django's stock UserAdmin is hardwired to
    ``username`` in list_display / search_fields / ordering / fieldsets /
    add_fieldsets — every reference is re-pointed at ``email`` (the
    USERNAME_FIELD) here."""

    form = CustomUserChangeForm
    add_form = CustomUserCreationForm
    model = CustomUser
    list_display = (
        "email",
        "first_name",
        "last_name",
        "email_confirmed",
        "is_active",
        "is_superuser",
    )
    list_filter = ("is_staff", "is_superuser", "is_active", "email_confirmed")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name", "language", "email_confirmed")}),
        (_("Permissions"), {"fields": ("is_active", "is_staff", "is_superuser")}),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2"),
            },
        ),
    )
    search_fields = ("email", "first_name", "last_name")
    ordering = ("email",)
    filter_horizontal = ()


admin.site.register(CustomUser, CustomUserAdmin)
admin.site.unregister(Group)

from django.urls import path, re_path

from .views import (
    AccountDeleteView,
    CalendarFeedView,
    CalendarTokenRotateView,
    ConfirmEmailView,
    LogoutView,
    MeView,
    PasswordChangeView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    RegisterView,
    ResendEmailView,
)

urlpatterns = [
    path("me/", MeView.as_view(), name="me"),
    path(
        "me/calendar-token/rotate/",
        CalendarTokenRotateView.as_view(),
        name="me_calendar_token_rotate",
    ),
    # Public per-user iCal feed. The token is URL-safe base64 (letters,
    # digits, '-', '_'); match up to the literal ".ics" suffix.
    re_path(
        r"^calendar/(?P<token>[\w-]+)\.ics$",
        CalendarFeedView.as_view(),
        name="calendar_feed",
    ),
    path("auth/register/", RegisterView.as_view(), name="auth_register"),
    path("auth/email/confirm/", ConfirmEmailView.as_view(), name="auth_email_confirm"),
    path("auth/email/resend/", ResendEmailView.as_view(), name="auth_email_resend"),
    path("auth/logout/", LogoutView.as_view(), name="auth_logout"),
    path(
        "auth/password/reset/",
        PasswordResetRequestView.as_view(),
        name="auth_password_reset",
    ),
    path(
        "auth/password/reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="auth_password_reset_confirm",
    ),
    path(
        "auth/password/change/",
        PasswordChangeView.as_view(),
        name="password-change",
    ),
    path(
        "auth/account/delete/",
        AccountDeleteView.as_view(),
        name="account-delete",
    ),
]

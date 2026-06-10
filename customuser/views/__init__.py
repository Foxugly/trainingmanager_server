"""Custom-user views, split by concern:

- ``profile``       — authenticated account self-service (MeView, RGPD export,
                      iCal feed + token rotation, verified email change,
                      password change, account deletion).
- ``registration``  — public self-signup (register, email confirm, resend).
- ``login``         — login lifecycle (verified JWT obtain, logout, magic-link
                      request/exchange).
- ``password_reset``— public password-reset request + confirm.

Re-exported flat so ``from customuser.views import X`` keeps working for
urls.py and the project URLconf.
"""

from .login import (
    LogoutView,
    MagicLinkExchangeView,
    MagicLinkRequestView,
    VerifiedTokenObtainPairView,
)
from .password_reset import PasswordResetConfirmView, PasswordResetRequestView
from .profile import (
    AccountDeleteView,
    CalendarFeedView,
    CalendarTokenRotateView,
    DataExportView,
    EmailChangeConfirmView,
    EmailChangeRequestView,
    MeView,
    PasswordChangeView,
)
from .registration import ConfirmEmailView, RegisterView, ResendEmailView

__all__ = [
    "AccountDeleteView",
    "CalendarFeedView",
    "CalendarTokenRotateView",
    "ConfirmEmailView",
    "DataExportView",
    "EmailChangeConfirmView",
    "EmailChangeRequestView",
    "LogoutView",
    "MagicLinkExchangeView",
    "MagicLinkRequestView",
    "MeView",
    "PasswordChangeView",
    "PasswordResetConfirmView",
    "PasswordResetRequestView",
    "RegisterView",
    "ResendEmailView",
    "VerifiedTokenObtainPairView",
]

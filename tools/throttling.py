from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class AIPingThrottle(UserRateThrottle):
    scope = "ai_ping"


class AIPlanGenerationThrottle(UserRateThrottle):
    scope = "ai_plan_generation"


class AITrainingGenerationThrottle(UserRateThrottle):
    scope = "ai_training_generation"


class AIReviewThrottle(UserRateThrottle):
    """Per-user throttle on the team training-block AI review."""

    scope = "ai_review"


# ---------------------------------------------------------------------
# Anonymous auth-flow throttles (per-IP). Rates configured in
# REST_FRAMEWORK.DEFAULT_THROTTLE_RATES in settings.
# ---------------------------------------------------------------------


class RegisterThrottle(AnonRateThrottle):
    """Anti-bot signup. Per-IP."""

    scope = "auth_register"


class ResendEmailThrottle(AnonRateThrottle):
    """Anti-enumeration + anti-mail-spam on /auth/email/resend/. Per-IP."""

    scope = "auth_resend_email"


class LoginThrottle(AnonRateThrottle):
    """Anti-bruteforce on /auth/token/. Per-IP, generous to avoid locking
    legit users on a typo storm."""

    scope = "auth_login"


class PasswordResetThrottle(AnonRateThrottle):
    """Anti-spam + anti-enumeration on /auth/password/reset/. Per-IP.
    Same rate as ResendEmailThrottle (3/hour) since both are "send a
    new email" patterns triggered by an unauthenticated user."""

    scope = "auth_password_reset"


class MagicLinkRequestThrottle(AnonRateThrottle):
    """Anti-spam + anti-enumeration on /auth/magic-link/request/. Per-IP.
    Same "send a new email" pattern as PasswordResetThrottle, triggered
    by an unauthenticated user."""

    scope = "auth_magic_link_request"


class LogoutThrottle(UserRateThrottle):
    """Per-user throttle on /auth/logout/.

    The endpoint requires a valid access token, so abuse is bounded to
    self-DOS — but a generous rate keeps the door closed against probing
    behaviours (e.g. an attacker who guessed a fresh access tries to
    blacklist many candidate refresh strings at once)."""

    scope = "auth_logout"


class ChangeEmailRequestThrottle(UserRateThrottle):
    """Per-user throttle on /me/email/change/ (authenticated) — anti-spam on the
    confirmation email sent to a user-supplied new address."""

    scope = "change_email_request"

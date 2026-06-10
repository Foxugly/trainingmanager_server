"""Small JWT/user payload helpers shared across the auth view modules
(registration confirm, password-reset confirm, magic-link exchange)."""

from rest_framework_simplejwt.tokens import RefreshToken


def _jwt_pair(user):
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def _user_payload(user):
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "language": user.language,
    }

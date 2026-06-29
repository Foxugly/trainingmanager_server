"""Firebase Cloud Messaging push sender.

Pattern-copied from PushIT_server (never imported). Uses a DEDICATED
TrainingManager Firebase project via ``settings.FCM_SERVICE_ACCOUNT_PATH``.
When that path is empty (local/dev/CI) the sender MOCKS: it logs and returns a
fake message id, so tests and unconfigured environments never touch FCM.
"""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)

_fcm_initialized = False


class PushProviderError(Exception):
    """Base class for FCM send failures."""


class InvalidPushTokenError(PushProviderError):
    """The token is unregistered/invalid; the device should be marked invalid."""


class TemporaryPushProviderError(PushProviderError):
    """A transient provider issue; the send may be retried later."""


def _ensure_fcm_initialized():
    global _fcm_initialized
    if _fcm_initialized:
        return
    import firebase_admin
    from firebase_admin import credentials

    if not firebase_admin._apps:
        cred = credentials.Certificate(settings.FCM_SERVICE_ACCOUNT_PATH)
        firebase_admin.initialize_app(cred)
    _fcm_initialized = True


def send_push_to_device(push_token, title, body, data=None, platform=None):
    """Send one push. Returns the provider message id (or a mock id).

    Raises ``InvalidPushTokenError`` for unregistered/invalid tokens,
    ``TemporaryPushProviderError`` for transient provider issues, and
    ``PushProviderError`` for any other FCM error.
    """
    if not getattr(settings, "FCM_SERVICE_ACCOUNT_PATH", ""):
        logger.info("FCM not configured; mocking push to %s…", push_token[:12])
        return "mock-message-id"

    _ensure_fcm_initialized()
    from firebase_admin import messaging
    from firebase_admin.exceptions import InvalidArgumentError, UnavailableError

    message = messaging.Message(
        token=push_token,
        notification=messaging.Notification(title=str(title), body=str(body)),
        data={str(k): str(v) for k, v in (data or {}).items()},
        android=messaging.AndroidConfig(priority="high"),
    )
    try:
        return messaging.send(message)
    except (messaging.UnregisteredError, InvalidArgumentError) as exc:
        raise InvalidPushTokenError(str(exc)) from exc
    except UnavailableError as exc:
        raise TemporaryPushProviderError(str(exc)) from exc
    except messaging.FirebaseError as exc:
        raise PushProviderError(str(exc)) from exc

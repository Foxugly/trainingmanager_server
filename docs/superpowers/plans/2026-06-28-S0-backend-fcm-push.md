# S0 — Backend FCM Push Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Firebase Cloud Messaging push to `trainingmanager_server` so every in-app notification can also reach the athlete's mobile devices, with a device registry and a `push` channel preference.

**Architecture:** A new `devices` app stores one FCM token per row, bound to the authenticated user (upsert by token). A `notifications/push.py` helper wraps the Firebase Admin SDK (mocked when unconfigured). The existing single fan-out point `notifications/services.py::notify` / `notify_many` gains a push channel gated by a new `NotificationPreference.push` flag — so all existing trigger sites become push-capable with zero call-site changes.

**Tech Stack:** Django 6.0.6, DRF 3.17, `firebase-admin==7.4.0`, pytest + pytest-django + factory-boy, drf-spectacular.

## Global Constraints

- Tests live in the **root `tests/`** dir (not per-app); pytest + factory-boy; fixtures in `tests/conftest.py` (`auth_client`, `authenticated_user`, `api_client`, …). Run with `pytest`.
- Lint: `ruff`/`ruff-format`/`black`, **line-length 100**, target py3.12. Pre-commit runs them.
- Error responses normalize to `{code, detail}`; raise validation with `gettext_lazy as _`.
- User-facing strings wrapped in `gettext_lazy as _`; error **code** strings are stable identifiers (no translation).
- App archetype: each app has `models.py` / `serializers.py` / `views.py` / `urls.py`, included in `django-trainingmanager/urls.py` under `path("api/v1/", include("<app>.urls"))`.
- OpenAPI is committed at `openapi-schema.yaml`; **0 spectacular warnings** maintained; regenerate in the same change: `python manage.py spectacular --file openapi-schema.yaml --validate`.
- **Isolation rule (vs PushIT):** this is pattern-copied from PushIT_server, never imported. Push uses a **dedicated TrainingManager Firebase project** — never PushIT's. The service-account JSON path comes from `FCM_SERVICE_ACCOUNT_PATH`; empty ⇒ mock mode.
- Settings package is `django-trainingmanager/settings/{base,dev,prod}.py`; `env = environ.Env(...)` is already defined in `base.py`. Tests use `…settings.dev`.
- All new endpoints require `IsAuthenticated` and scope to `request.user`.

---

### Task 1: `devices` app — model

**Files:**
- Create: `devices/__init__.py`, `devices/apps.py`, `devices/models.py`, `devices/admin.py`, `devices/migrations/__init__.py`
- Modify: `django-trainingmanager/settings/base.py` (add `"devices"` to `INSTALLED_APPS`)
- Test: `tests/test_devices.py`

**Interfaces:**
- Produces: `devices.models.Device` (fields `user, push_token, platform, status, device_name, last_seen_at, failure_count, created_at, updated_at`); `devices.models.DevicePlatform` (`ANDROID="android"`, `IOS="ios"`); `devices.models.DeviceTokenStatus` (`ACTIVE="active"`, `INVALID="invalid"`, `REVOKED="revoked"`).

- [ ] **Step 1: Scaffold the app**

Run: `python manage.py startapp devices`
Then set the app config in `devices/apps.py`:

```python
from django.apps import AppConfig


class DevicesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "devices"
```

- [ ] **Step 2: Register the app**

In `django-trainingmanager/settings/base.py`, add `"devices",` to the project's `INSTALLED_APPS` list (alongside the other first-party apps such as `"notifications"`).

- [ ] **Step 3: Write the model**

Replace `devices/models.py` with:

```python
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class DevicePlatform(models.TextChoices):
    ANDROID = "android", _("Android")
    IOS = "ios", _("iOS")


class DeviceTokenStatus(models.TextChoices):
    ACTIVE = "active", _("Active")
    INVALID = "invalid", _("Invalid")
    REVOKED = "revoked", _("Revoked")


class Device(models.Model):
    """An FCM-capable mobile device belonging to one authenticated user.

    Identity is the FCM ``push_token`` (unique). Registration upserts by token
    and (re)binds it to the current user, so a re-used device row follows its
    latest owner. ``status`` tracks token health: a send that hits an
    Unregistered/invalid token flips the row to ``invalid`` and future sends
    skip it; logout sets ``revoked``.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="devices",
    )
    push_token = models.CharField(max_length=512, unique=True)
    platform = models.CharField(max_length=20, choices=DevicePlatform.choices)
    status = models.CharField(
        max_length=20,
        choices=DeviceTokenStatus.choices,
        default=DeviceTokenStatus.ACTIVE,
    )
    device_name = models.CharField(max_length=120, blank=True, default="")
    last_seen_at = models.DateTimeField(null=True, blank=True)
    failure_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["user", "status"])]

    def __str__(self):
        return f"{self.platform} device of {self.user} ({self.status})"
```

- [ ] **Step 4: Write the failing test**

Create `tests/test_devices.py`:

```python
import pytest

from devices.models import Device, DevicePlatform, DeviceTokenStatus


@pytest.mark.django_db
def test_device_defaults_active(authenticated_user):
    device = Device.objects.create(
        user=authenticated_user,
        push_token="x" * 40,
        platform=DevicePlatform.ANDROID,
    )
    assert device.status == DeviceTokenStatus.ACTIVE
    assert device.failure_count == 0
    assert device.created_at is not None
```

- [ ] **Step 5: Make the migration and run the test**

Run: `python manage.py makemigrations devices && pytest tests/test_devices.py -q`
Expected: migration `devices/migrations/0001_initial.py` created; test PASSES.

- [ ] **Step 6: Commit**

```bash
git add devices/ tests/test_devices.py django-trainingmanager/settings/base.py
git commit -m "feat(devices): add Device model for FCM push tokens"
```

---

### Task 2: `devices` register / unregister / list API

**Files:**
- Create: `devices/serializers.py`, `devices/views.py`, `devices/urls.py`
- Modify: `django-trainingmanager/urls.py` (include `devices.urls`)
- Test: `tests/test_devices_api.py`

**Interfaces:**
- Consumes: `devices.models.Device`, `DevicePlatform`, `DeviceTokenStatus` (Task 1).
- Produces: `POST /api/v1/devices/register/` (`{push_token, platform, device_name?}` → `DeviceRead`, 201 new / 200 existing); `POST /api/v1/devices/unregister/` (`{push_token}` → 204); `GET /api/v1/devices/` (→ `[DeviceRead]`). `DeviceReadSerializer` fields: `id, platform, status, device_name, last_seen_at, created_at`.

- [ ] **Step 1: Write the serializers**

Create `devices/serializers.py`:

```python
from rest_framework import serializers

from .models import Device, DevicePlatform


class DeviceRegisterSerializer(serializers.Serializer):
    push_token = serializers.CharField(min_length=20, max_length=512)
    platform = serializers.ChoiceField(choices=DevicePlatform.choices)
    device_name = serializers.CharField(
        max_length=120, required=False, allow_blank=True, default=""
    )


class DeviceUnregisterSerializer(serializers.Serializer):
    push_token = serializers.CharField(min_length=20, max_length=512)


class DeviceReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Device
        fields = ["id", "platform", "status", "device_name", "last_seen_at", "created_at"]
        read_only_fields = fields
```

- [ ] **Step 2: Write the views**

Create `devices/views.py`:

```python
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Device, DeviceTokenStatus
from .serializers import (
    DeviceReadSerializer,
    DeviceRegisterSerializer,
    DeviceUnregisterSerializer,
)


class DeviceRegisterView(APIView):
    """Upsert the caller's device by FCM token (idempotent; call on launch + token rotation)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=DeviceRegisterSerializer, responses=DeviceReadSerializer)
    def post(self, request):
        serializer = DeviceRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        device, created = Device.objects.update_or_create(
            push_token=data["push_token"],
            defaults={
                "user": request.user,
                "platform": data["platform"],
                "device_name": data.get("device_name", ""),
                "status": DeviceTokenStatus.ACTIVE,
                "last_seen_at": timezone.now(),
                "failure_count": 0,
            },
        )
        return Response(
            DeviceReadSerializer(device).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class DeviceUnregisterView(APIView):
    """Revoke the caller's device with the given token (call on logout)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=DeviceUnregisterSerializer, responses=None)
    def post(self, request):
        serializer = DeviceUnregisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        Device.objects.filter(
            user=request.user, push_token=serializer.validated_data["push_token"]
        ).update(status=DeviceTokenStatus.REVOKED)
        return Response(status=status.HTTP_204_NO_CONTENT)


class DeviceListView(APIView):
    """List the caller's active devices."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=DeviceReadSerializer(many=True))
    def get(self, request):
        qs = Device.objects.filter(user=request.user, status=DeviceTokenStatus.ACTIVE)
        return Response(DeviceReadSerializer(qs, many=True).data)
```

- [ ] **Step 3: Write the URLs and include them**

Create `devices/urls.py`:

```python
from django.urls import path

from .views import DeviceListView, DeviceRegisterView, DeviceUnregisterView

urlpatterns = [
    path("devices/register/", DeviceRegisterView.as_view(), name="device-register"),
    path("devices/unregister/", DeviceUnregisterView.as_view(), name="device-unregister"),
    path("devices/", DeviceListView.as_view(), name="device-list"),
]
```

In `django-trainingmanager/urls.py`, add next to the other includes:

```python
    path("api/v1/", include("devices.urls")),
```

- [ ] **Step 4: Write the failing tests**

Create `tests/test_devices_api.py`:

```python
import pytest

from devices.models import Device, DeviceTokenStatus

TOKEN = "f" * 40


@pytest.mark.django_db
def test_register_creates_device(auth_client, authenticated_user):
    resp = auth_client.post(
        "/api/v1/devices/register/",
        {"push_token": TOKEN, "platform": "android"},
        format="json",
    )
    assert resp.status_code == 201
    device = Device.objects.get(push_token=TOKEN)
    assert device.user == authenticated_user
    assert device.status == DeviceTokenStatus.ACTIVE


@pytest.mark.django_db
def test_register_is_idempotent_upsert(auth_client):
    auth_client.post(
        "/api/v1/devices/register/",
        {"push_token": TOKEN, "platform": "android"},
        format="json",
    )
    resp = auth_client.post(
        "/api/v1/devices/register/",
        {"push_token": TOKEN, "platform": "ios", "device_name": "iPhone"},
        format="json",
    )
    assert resp.status_code == 200
    assert Device.objects.filter(push_token=TOKEN).count() == 1
    device = Device.objects.get(push_token=TOKEN)
    assert device.platform == "ios"
    assert device.device_name == "iPhone"


@pytest.mark.django_db
def test_unregister_revokes(auth_client):
    auth_client.post(
        "/api/v1/devices/register/",
        {"push_token": TOKEN, "platform": "android"},
        format="json",
    )
    resp = auth_client.post(
        "/api/v1/devices/unregister/", {"push_token": TOKEN}, format="json"
    )
    assert resp.status_code == 204
    assert Device.objects.get(push_token=TOKEN).status == DeviceTokenStatus.REVOKED


@pytest.mark.django_db
def test_list_returns_only_active_for_caller(auth_client):
    auth_client.post(
        "/api/v1/devices/register/",
        {"push_token": TOKEN, "platform": "android"},
        format="json",
    )
    resp = auth_client.get("/api/v1/devices/")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


@pytest.mark.django_db
def test_register_requires_auth(api_client):
    resp = api_client.post(
        "/api/v1/devices/register/",
        {"push_token": TOKEN, "platform": "android"},
        format="json",
    )
    assert resp.status_code == 401
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_devices_api.py -q`
Expected: all 5 PASS.

- [ ] **Step 6: Commit**

```bash
git add devices/ tests/test_devices_api.py django-trainingmanager/urls.py
git commit -m "feat(devices): register/unregister/list endpoints"
```

---

### Task 3: `push` channel in NotificationPreference + preference matrix

**Files:**
- Modify: `notifications/models.py` (add `push` field), `notifications/services.py:get_effective_preferences` (+ `DEFAULT_PUSH`), `notifications/serializers.py:NotificationPreferenceSerializer`, `notifications/views.py:preferences` (PUT defaults)
- Test: `tests/test_notification_preferences_push.py`

**Interfaces:**
- Consumes: existing `notify`/preferences machinery.
- Produces: `NotificationPreference.push` (BooleanField default True); `get_effective_preferences(user)` items now `{type, label, in_app, email, push}`; `DEFAULT_PUSH = True` in `notifications/services.py`. (`_resolve_channels` is **unchanged here** — extended in Task 5.)

- [ ] **Step 1: Add the model field**

In `notifications/models.py`, in `NotificationPreference`, add after the `email` field:

```python
    push = models.BooleanField(default=True)
```

- [ ] **Step 2: Make the migration**

Run: `python manage.py makemigrations notifications`
Expected: a migration adding `push` (default True) is created.

- [ ] **Step 3: Expose `push` in the matrix + serializer + view**

In `notifications/services.py`, add the default constant next to the others:

```python
DEFAULT_PUSH = True
```

and in `get_effective_preferences`, add `push` to each appended dict:

```python
        effective.append(
            {
                "type": value,
                "label": str(label),
                "in_app": pref.in_app if pref is not None else DEFAULT_IN_APP,
                "email": pref.email if pref is not None else DEFAULT_EMAIL,
                "push": pref.push if pref is not None else DEFAULT_PUSH,
            }
        )
```

In `notifications/serializers.py`, add to `NotificationPreferenceSerializer` (after `email`):

```python
    push = serializers.BooleanField(required=False, default=True)
```

In `notifications/views.py`, in `preferences()`'s PUT loop, add `push` to the `defaults`:

```python
                    defaults={
                        "in_app": entry["in_app"],
                        "email": entry["email"],
                        "push": entry["push"],
                    },
```

- [ ] **Step 4: Write the failing tests**

Create `tests/test_notification_preferences_push.py`:

```python
import pytest

from notifications.models import NotificationPreference, NotificationType


@pytest.mark.django_db
def test_preferences_get_defaults_push_true(auth_client):
    resp = auth_client.get("/api/v1/notifications/preferences/")
    assert resp.status_code == 200
    assert all(row["push"] is True for row in resp.json())


@pytest.mark.django_db
def test_preferences_put_sets_push(auth_client, authenticated_user):
    payload = {
        "preferences": [
            {
                "type": NotificationType.MESSAGE_NEW_REPLY.value,
                "in_app": True,
                "email": False,
                "push": False,
            }
        ]
    }
    resp = auth_client.put(
        "/api/v1/notifications/preferences/", payload, format="json"
    )
    assert resp.status_code == 200
    pref = NotificationPreference.objects.get(
        user=authenticated_user, type=NotificationType.MESSAGE_NEW_REPLY.value
    )
    assert pref.push is False
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_notification_preferences_push.py -q`
Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add notifications/ tests/test_notification_preferences_push.py
git commit -m "feat(notifications): add push channel preference"
```

---

### Task 4: `notifications/push.py` — FCM sender (+ settings + dependency)

**Files:**
- Create: `notifications/push.py`
- Modify: `requirements.txt` (add `firebase-admin==7.4.0`), `django-trainingmanager/settings/base.py` (FCM settings)
- Test: `tests/test_push.py`

**Interfaces:**
- Produces: `notifications.push.send_push_to_device(push_token, title, body, data=None, platform=None) -> str`; exceptions `PushProviderError`, `InvalidPushTokenError`, `TemporaryPushProviderError`. Mock-returns `"mock-message-id"` when `settings.FCM_SERVICE_ACCOUNT_PATH` is empty.

- [ ] **Step 1: Add the dependency + settings**

In `requirements.txt`, add (after `anthropic==0.97.0`):

```
firebase-admin==7.4.0
```

Install it now: `pip install firebase-admin==7.4.0`

In `django-trainingmanager/settings/base.py`, add near the other `env(...)` reads:

```python
# --- Push (FCM) — dedicated TrainingManager Firebase project (never PushIT's).
# Empty path => mock mode (local/dev/CI): pushes are logged, not sent.
FCM_SERVICE_ACCOUNT_PATH = env("FCM_SERVICE_ACCOUNT_PATH", default="")
```

- [ ] **Step 2: Write the sender**

Create `notifications/push.py`:

```python
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
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_push.py`:

```python
import pytest

from notifications import push
from notifications.push import InvalidPushTokenError, send_push_to_device


def test_send_is_mocked_when_unconfigured(settings):
    settings.FCM_SERVICE_ACCOUNT_PATH = ""
    assert send_push_to_device("t" * 40, "Hi", "Body") == "mock-message-id"


def test_invalid_token_maps_to_invalid_push_token_error(settings, monkeypatch):
    settings.FCM_SERVICE_ACCOUNT_PATH = "/tmp/fake-service-account.json"
    monkeypatch.setattr(push, "_ensure_fcm_initialized", lambda: None)
    from firebase_admin import messaging

    def _raise(_message):
        raise messaging.UnregisteredError("token gone")

    monkeypatch.setattr(messaging, "send", _raise)
    with pytest.raises(InvalidPushTokenError):
        send_push_to_device("t" * 40, "Hi", "Body")
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_push.py -q`
Expected: both PASS. (Requires `firebase-admin` installed from Step 1.)

- [ ] **Step 5: Commit**

```bash
git add notifications/push.py tests/test_push.py requirements.txt django-trainingmanager/settings/base.py
git commit -m "feat(notifications): FCM push sender with mock fallback"
```

---

### Task 5: Wire push into `notify` / `notify_many`

**Files:**
- Modify: `notifications/services.py` (`_resolve_channels` → 3-tuple; add `_push_to_user_devices`; push send in `notify` + `notify_many`; imports)
- Test: `tests/test_notify_push.py`

**Interfaces:**
- Consumes: `devices.models.Device`/`DeviceTokenStatus` (Task 1), `notifications.push.send_push_to_device` + exceptions (Task 4), `DEFAULT_PUSH` (Task 3).
- Produces: `notify()` / `notify_many()` now send push to the recipient's active devices when the in-app row is created AND the recipient's `push` preference is on; the FCM `data` payload is `{"type": <type>, "url": <url>, "notification_id": <id>}`. Invalid tokens flip the device to `invalid`.

- [ ] **Step 1: Add imports + the push helper**

In `notifications/services.py`, add to the imports at the top:

```python
from django.db.models import F
from django.utils import timezone

from devices.models import DeviceTokenStatus
from .push import InvalidPushTokenError, PushProviderError, send_push_to_device
```

Add this helper below `_resolve_channels`:

```python
def _push_to_user_devices(recipient, title, body, data):
    """Best-effort push to all the recipient's active devices.

    Invalid tokens flip the device to ``invalid`` (skipped next time); other
    provider errors bump ``failure_count`` and are swallowed so a flaky push
    never breaks the triggering request.
    """
    for device in recipient.devices.filter(status=DeviceTokenStatus.ACTIVE):
        try:
            send_push_to_device(
                device.push_token, title, body, data=data, platform=device.platform
            )
            device.last_seen_at = timezone.now()
            device.failure_count = 0
            device.save(update_fields=["last_seen_at", "failure_count"])
        except InvalidPushTokenError:
            device.status = DeviceTokenStatus.INVALID
            device.save(update_fields=["status"])
        except PushProviderError:
            type(device).objects.filter(pk=device.pk).update(
                failure_count=F("failure_count") + 1
            )
```

- [ ] **Step 2: Extend `_resolve_channels` to include push**

Replace `_resolve_channels` with:

```python
def _resolve_channels(recipient, type):
    """Resolve (in_app, email, push) booleans for one recipient + type,
    applying defaults when no preference row exists."""
    pref = NotificationPreference.objects.filter(user=recipient, type=type).first()
    if pref is None:
        return DEFAULT_IN_APP, DEFAULT_EMAIL, DEFAULT_PUSH
    return pref.in_app, pref.email, pref.push
```

- [ ] **Step 3: Send push from `notify`**

In `notify()`, replace the channel-resolution + in-app block. The line
`in_app, email = _resolve_channels(recipient, type)` becomes
`in_app, email, push = _resolve_channels(recipient, type)`, and the in-app
block becomes:

```python
    created = None
    if in_app:
        with translation.override(getattr(recipient, "language", None) or "en"):
            created = Notification.objects.create(
                recipient=recipient,
                type=type,
                title=str(title),
                body=str(body),
                url=url,
            )
            if push:
                _push_to_user_devices(
                    recipient,
                    str(title),
                    str(body),
                    data={"type": type, "url": url, "notification_id": created.id},
                )
```

- [ ] **Step 4: Send push from `notify_many`**

In `notify_many()`:

(a) the prefs dict comprehension becomes a 3-tuple:

```python
    prefs = {
        p.user_id: (p.in_app, p.email, p.push)
        for p in NotificationPreference.objects.filter(user__in=targets, type=type)
    }
```

(b) track per-recipient push alongside the in-app list. Replace the build loop's
unpack + in-app branch so it also records `push`:

```python
    to_create = []
    in_app_recipients = []
    in_app_push_flags = []
    email_recipients = []
    for recipient in targets:
        in_app, email, push = prefs.get(
            recipient.pk, (DEFAULT_IN_APP, DEFAULT_EMAIL, DEFAULT_PUSH)
        )
        if in_app:
            with translation.override(getattr(recipient, "language", None) or "en"):
                to_create.append(
                    Notification(
                        recipient=recipient,
                        type=type,
                        title=str(title),
                        body=str(body),
                        url=url,
                    )
                )
            in_app_recipients.append(recipient)
            in_app_push_flags.append(push)
        if (
            email
            and getattr(recipient, "email", None)
            and not getattr(recipient, "digest_email", False)
        ):
            email_recipients.append(recipient)
```

(c) after `created = Notification.objects.bulk_create(to_create)`, push per created row:

```python
    for recipient, notif, push in zip(in_app_recipients, created, in_app_push_flags):
        if push:
            with translation.override(getattr(recipient, "language", None) or "en"):
                _push_to_user_devices(
                    recipient,
                    str(title),
                    str(body),
                    data={"type": type, "url": url, "notification_id": notif.id},
                )
```

- [ ] **Step 5: Write the failing tests**

Create `tests/test_notify_push.py`:

```python
import pytest

from devices.models import Device, DevicePlatform, DeviceTokenStatus
from notifications import services
from notifications.models import NotificationPreference, NotificationType

TYPE = NotificationType.MESSAGE_NEW_REPLY.value


@pytest.fixture
def recipient_with_device(authenticated_user):
    Device.objects.create(
        user=authenticated_user,
        push_token="d" * 40,
        platform=DevicePlatform.ANDROID,
    )
    return authenticated_user


@pytest.mark.django_db
def test_notify_pushes_to_active_device(recipient_with_device, monkeypatch):
    calls = []
    monkeypatch.setattr(
        services,
        "send_push_to_device",
        lambda token, title, body, data=None, platform=None: calls.append(data) or "id",
    )
    services.notify(recipient_with_device, TYPE, "Title", "Body", url="/teams/3")
    assert len(calls) == 1
    assert calls[0]["type"] == TYPE
    assert calls[0]["url"] == "/teams/3"
    assert "notification_id" in calls[0]


@pytest.mark.django_db
def test_notify_skips_push_when_pref_off(recipient_with_device, monkeypatch):
    NotificationPreference.objects.create(
        user=recipient_with_device, type=TYPE, in_app=True, email=False, push=False
    )
    calls = []
    monkeypatch.setattr(
        services,
        "send_push_to_device",
        lambda *a, **k: calls.append(1) or "id",
    )
    services.notify(recipient_with_device, TYPE, "Title", "Body")
    assert calls == []


@pytest.mark.django_db
def test_notify_marks_device_invalid_on_bad_token(recipient_with_device, monkeypatch):
    def _raise(*a, **k):
        raise services.InvalidPushTokenError("gone")

    monkeypatch.setattr(services, "send_push_to_device", _raise)
    services.notify(recipient_with_device, TYPE, "Title", "Body")
    device = Device.objects.get(push_token="d" * 40)
    assert device.status == DeviceTokenStatus.INVALID


@pytest.mark.django_db
def test_notify_no_push_to_actor(recipient_with_device, monkeypatch):
    calls = []
    monkeypatch.setattr(
        services, "send_push_to_device", lambda *a, **k: calls.append(1) or "id"
    )
    # actor == recipient => nothing happens at all
    services.notify(
        recipient_with_device, TYPE, "Title", "Body", actor=recipient_with_device
    )
    assert calls == []
```

> Note: `tests/test_notify_push.py` references `services.InvalidPushTokenError`; it is
> importable there because Task 5 Step 1 imports it into `services`.

- [ ] **Step 6: Run the tests**

Run: `pytest tests/test_notify_push.py -q`
Expected: all 4 PASS. Then run the notifications regression set:
`pytest tests/ -k "notif or push or device" -q` — all green.

- [ ] **Step 7: Commit**

```bash
git add notifications/services.py tests/test_notify_push.py
git commit -m "feat(notifications): deliver push alongside in-app notifications"
```

---

### Task 6: Regenerate the OpenAPI contract

**Files:**
- Modify: `openapi-schema.yaml`

- [ ] **Step 1: Regenerate + validate**

Run: `python manage.py spectacular --file openapi-schema.yaml --validate`
Expected: exits cleanly, **0 warnings**. The schema now contains `/api/v1/devices/register/`, `/unregister/`, `/devices/`, and the `push` field on the notification-preference schemas.

- [ ] **Step 2: Sanity-check the diff is real (CRLF gotcha)**

If `git status` shows `openapi-schema.yaml` modified, confirm it's a real change (not CRLF-only):
Run: `diff <(tr -d '\r' < openapi-schema.yaml) <(git show HEAD:openapi-schema.yaml | tr -d '\r') | head`
Expected: shows the new `devices` paths + `push` field. If empty, `git checkout openapi-schema.yaml`.

- [ ] **Step 3: Full suite**

Run: `pytest -q`
Expected: green (no regressions).

- [ ] **Step 4: Commit**

```bash
git add openapi-schema.yaml
git commit -m "chore(openapi): add devices endpoints + push preference"
```

---

## Self-Review

**1. Spec coverage (against S0 spec §4–§11):**
- Device model (§4) → Task 1. Register/unregister/list (§5) → Task 2. Sender + exceptions + mock + settings + dependency (§6, §8) → Task 4. `notify()` hook + `_push_to_user_devices` + `data` contract (§6–§7) → Task 5. `notify_many()` also hooked (the spec named `notify()`; `notify_many` is the bulk twin and is covered in Task 5). `NotificationPreference.push` + endpoint exposure (§7) → Task 3. OpenAPI regen (§9) → Task 6. Tests (§10) → Tasks 1–5. **No gaps.**
- Out of scope confirmed unbuilt: Celery/async (synchronous best-effort send), session-reminder cron, PushIT app-token machinery.

**2. Placeholder scan:** none — every code/test/command step has concrete content.

**3. Type consistency:** `_resolve_channels` returns a 3-tuple after Task 5 and every caller (`notify`, `notify_many`) unpacks three; `get_effective_preferences` items carry `push` (Task 3) consumed by the serializer (Task 3). `send_push_to_device(push_token, title, body, data=None, platform=None)` signature is identical in Task 4 (definition), the Task 5 helper call, and the Task 5 test monkeypatches. `data` payload keys (`type`, `url`, `notification_id`) match between `notify`, `notify_many`, and the Task 5 assertion. `Device` field names match across model, serializer, views, and tests.

**Open dependency (owner):** before prod, provision the dedicated TM Firebase service account and set `FCM_SERVICE_ACCOUNT_PATH` (SSM→/run, e.g. `/trainingmanager/prod/fcm-service-account`). Until then the sender mocks — all tests pass without it.

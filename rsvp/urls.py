"""URL config for the rsvp app.

One namespace, nested under events (mirrors roti/urls.py's nesting):
- /api/v1/events/{event_pk}/rsvp/                       GET (summary incl. my_status), PUT (upsert own)
- /api/v1/events/{event_pk}/rsvp/apply_to_attendance/  POST (managers pre-fill attendance)

RSVP is a thin ViewSet (not a ModelViewSet): the collection URL itself
carries GET (summary incl. my_status) and PUT (update -> upsert own status),
so the methods are mapped explicitly via as_view(). The GET handler is named
``summary`` (not ``list``) so drf-spectacular emits a single-object response
schema instead of array-wrapping it as a collection.
"""

from django.urls import path

from .views import RsvpViewSet

rsvp_collection = RsvpViewSet.as_view({"get": "summary", "put": "update"})
rsvp_apply_to_attendance = RsvpViewSet.as_view({"post": "apply_to_attendance"})

urlpatterns = [
    path(
        "events/<int:event_pk>/rsvp/",
        rsvp_collection,
        name="event-rsvp-list",
    ),
    path(
        "events/<int:event_pk>/rsvp/apply_to_attendance/",
        rsvp_apply_to_attendance,
        name="event-rsvp-apply-to-attendance",
    ),
]

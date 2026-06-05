"""URL config for the roti app.

One namespace, nested under events (mirrors attendance/urls.py's nesting):
- /api/v1/events/{event_pk}/roti/          GET (summary incl. my_score), PUT (upsert own)
- /api/v1/events/{event_pk}/roti/summary/  GET (manager aggregate)

ROTI is a thin ViewSet (not a ModelViewSet): the collection URL itself
carries GET (list -> summary) and PUT (update -> upsert own score), so the
methods are mapped explicitly via as_view() rather than via a router's
default list route (which only binds GET/POST).
"""

from django.urls import path

from .views import RotiViewSet

roti_collection = RotiViewSet.as_view({"get": "list", "put": "update"})
roti_summary = RotiViewSet.as_view({"get": "summary"})

urlpatterns = [
    path(
        "events/<int:event_pk>/roti/",
        roti_collection,
        name="event-roti-list",
    ),
    path(
        "events/<int:event_pk>/roti/summary/",
        roti_summary,
        name="event-roti-summary",
    ),
]

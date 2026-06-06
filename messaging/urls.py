"""Nested routing for the messaging app.

- /api/v1/teams/{team_pk}/topics/
- /api/v1/teams/{team_pk}/topics/{topic_pk}/messages/

Mirrors note/urls.py and chat/urls.py: build a local parent router chain
so we can plug the topic + message viewsets under the existing teams URL
space without re-emitting team routes. Unique basenames avoid collisions
with the routes served by the team app.
"""

from rest_framework.routers import DefaultRouter
from rest_framework_nested.routers import NestedSimpleRouter

from team.views import TeamViewSet

from .views import TopicMessageViewSet, TopicViewSet

_parent = DefaultRouter()
_parent.register(r"teams", TeamViewSet, basename="messaging-parent-team")

topics_router = NestedSimpleRouter(_parent, r"teams", lookup="team")
topics_router.register(r"topics", TopicViewSet, basename="team-topic")

messages_router = NestedSimpleRouter(topics_router, r"topics", lookup="topic")
messages_router.register(r"messages", TopicMessageViewSet, basename="topic-message")

urlpatterns = topics_router.urls + messages_router.urls

from rest_framework.routers import DefaultRouter

from .views import EventTemplateViewSet, EventViewSet

router = DefaultRouter()
router.register(r"events", EventViewSet, basename="event")
router.register(r"event-templates", EventTemplateViewSet, basename="event-template")

urlpatterns = router.urls

from rest_framework.routers import DefaultRouter

from .views import LevelViewSet

router = DefaultRouter()
router.register(r"levels", LevelViewSet, basename="level")

urlpatterns = router.urls

from rest_framework.routers import DefaultRouter

from .views import PerformanceViewSet

router = DefaultRouter()
router.register(r"performances", PerformanceViewSet, basename="performance")

urlpatterns = router.urls

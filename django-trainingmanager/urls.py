from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework_simplejwt.views import TokenRefreshView

from customuser.views import VerifiedTokenObtainPairView
from event.public_views import PublicEventView
from tools.health import HealthCheckView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/health/", HealthCheckView.as_view(), name="healthcheck"),
    # Auth API (JWT). VerifiedTokenObtainPairView refuses login for users
    # whose primary email is not yet verified — see customuser/serializers.py.
    path(
        "api/v1/auth/token/",
        VerifiedTokenObtainPairView.as_view(),
        name="token_obtain_pair",
    ),
    path("api/v1/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    # CustomUser (/me/)
    path("api/v1/", include("customuser.urls")),
    # Teams
    path("api/v1/", include("team.urls")),
    # AI endpoints
    path("api/v1/", include("ai.urls")),
    path("api/v1/", include("aiusage.urls")),
    # Endpoints métier
    path("api/v1/", include("sport.urls")),
    path("api/v1/", include("program.urls")),
    path("api/v1/", include("event.urls")),
    path("api/v1/", include("round.urls")),
    path("api/v1/", include("exercise.urls")),
    path("api/v1/", include("member.urls")),
    path("api/v1/", include("note.urls")),
    path("api/v1/", include("chat.urls")),
    path("api/v1/", include("attendance.urls")),
    path("api/v1/", include("roti.urls")),
    path("api/v1/", include("rsvp.urls")),
    path("api/v1/", include("level.urls")),
    path("api/v1/", include("notifications.urls")),
    path("api/v1/", include("messaging.urls")),
    path("api/v1/", include("attachment.urls")),
    # Public (unauthenticated) read-only session share link
    path(
        "api/v1/public/events/<str:token>/",
        PublicEventView.as_view(),
        name="public_event",
    ),
    # OpenAPI schema + Swagger UI
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/v1/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]

if settings.DEBUG:
    urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]

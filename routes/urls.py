from django.urls import path

# This is a workaround for DRF's inconsistent URL naming
from django.views.decorators.csrf import csrf_exempt
from rest_framework.routers import DefaultRouter

from .views import RouteViewSet, StopViewSet

router = DefaultRouter()
router.register(r"routes", RouteViewSet, basename="route")
router.register(r"stops", StopViewSet, basename="stop")

# Get URL patterns from router
urlpatterns = list(router.urls)

for urlpattern in router.urls:
    if hasattr(urlpattern, "pattern"):
        if hasattr(urlpattern, "name") and urlpattern.name:
            print(f"NAME: {urlpattern.name}")
        print("-" * 30)

# Add the endpoint manually to ensure reverse() works
urlpatterns.append(
    path(
        "routes/calculate-fastest/",
        csrf_exempt(RouteViewSet.as_view({"post": "calculate_fastest_route"})),
        name="route-calculate-fastest",
    )
)

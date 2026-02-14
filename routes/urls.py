from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from rest_framework.routers import DefaultRouter
from .views import RouteViewSet, StopViewSet, geocode_search

router = DefaultRouter()
router.register(r"routes", RouteViewSet, basename="route")
router.register(r"stops", StopViewSet, basename="stop")

# Get URL patterns from router
urlpatterns = list(router.urls)

# Add the endpoint manually to ensure reverse() works
urlpatterns.append(
    path(
        "routes/calculate-fastest/",
        csrf_exempt(RouteViewSet.as_view({"post": "calculate_fastest_route"})),
        name="route-calculate-fastest",
    )
)

urlpatterns.append(
    path(
        "routes/calculate-scenic/",
        csrf_exempt(RouteViewSet.as_view({"post": "calculate_scenic_route"})),
        name="route-calculate-scenic",
    )
)

urlpatterns.append(
    path(
        "geocode/search/",
        geocode_search,
        name="geocode-search",
    )
)
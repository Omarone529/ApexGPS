from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from rest_framework.routers import DefaultRouter
from .views import RouteViewSet, StopViewSet, geocode_search, poi_photos

router = DefaultRouter()
router.register(r"routes", RouteViewSet, basename="route")
router.register(r"stops", StopViewSet, basename="stop")

urlpatterns = list(router.urls)

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

urlpatterns.append(
    path(
        "pois/photos/",
        poi_photos,
        name="poi-photos",
    )
)
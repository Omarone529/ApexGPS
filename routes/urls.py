from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import RouteViewSet, StopViewSet

router = DefaultRouter()
router.register(r"routes", RouteViewSet, basename="route")
router.register(r"stops", StopViewSet, basename="stop")

urlpatterns = [
    path("", include(router.urls)),
]

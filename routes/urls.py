from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import RouteViewSet

router = DefaultRouter()
router.register(r"routes", RouteViewSet, basename="route")

urlpatterns = [
    path("", include(router.urls)),
]

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(
    r"elevation-queries", views.ElevationQueryViewSet, basename="elevationquery"
)
router.register(r"dem", views.DEMViewSet, basename="dem")

urlpatterns = [
    path("", include(router.urls)),
]

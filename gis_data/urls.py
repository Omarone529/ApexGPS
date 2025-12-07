from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(
    r"points-of-interest", views.PointOfInterestViewSet, basename="pointofinterest"
)
router.register(r"scenic-areas", views.ScenicAreaViewSet, basename="scenicarea")

# The API URLs are now determined automatically by the router
urlpatterns = [
    path("", include(router.urls)),
]

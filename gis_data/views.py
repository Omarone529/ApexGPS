from rest_framework import viewsets

from users.permissions import IsAdminUser, IsRegisteredUser

from .models import PointOfInterest, ScenicArea
from .serializers import PointOfInterestSerializer, ScenicAreaSerializer


class ProtectedResourceViewSet(viewsets.ModelViewSet):
    """
    Base ViewSet for protected resources with role-based permissions.

    Permission rules:
    - Anyone can view (GET)
    - Only registered users can create (POST)
    - Only administrators can update or delete (PUT, PATCH, DELETE)
    """

    def get_permissions(self):
        """Return a list of permissions needed to access the view."""
        if self.action in ["list", "retrieve"]:
            return []
        elif self.action == "create":
            return [IsRegisteredUser()]
        elif self.action in ["update", "partial_update", "destroy"]:
            return [IsAdminUser()]
        return []


class PointOfInterestViewSet(ProtectedResourceViewSet):
    """
    ViewSet for PointOfInterest model.
    Provides CRUD operations for points of interest.
    """

    queryset = PointOfInterest.objects.all()
    serializer_class = PointOfInterestSerializer


class ScenicAreaViewSet(ProtectedResourceViewSet):
    """
    ViewSet for ScenicArea model.
    Provides CRUD operations for scenic areas.
    """

    queryset = ScenicArea.objects.all()
    serializer_class = ScenicAreaSerializer

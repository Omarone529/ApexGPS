from rest_framework import viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from users.permissions import IsAdminUser, IsRegisteredUser

from .models import PointOfInterest, ScenicArea, RoadSegment
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

@api_view(['GET'])
@permission_classes([AllowAny])
def stats_view(request):
    """
    Public endpoint returning the count of active POIs and road segments.
    """
    poi_count = PointOfInterest.objects.filter(is_active=True).count()
    segment_count = RoadSegment.objects.filter(is_active=True).count()
    return Response({
        'poi_count': poi_count,
        'segment_count': segment_count,
    })

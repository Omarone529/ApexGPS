from rest_framework import viewsets

from .models import PointOfInterest, ScenicArea
from .serializers import PointOfInterestSerializer, ScenicAreaSerializer


class PointOfInterestViewSet(viewsets.ModelViewSet):
    """
    ViewSet for PointOfInterest model.
    Provides CRUD operations for points of interest.
    """

    queryset = PointOfInterest.objects.all()
    serializer_class = PointOfInterestSerializer


class ScenicAreaViewSet(viewsets.ModelViewSet):
    """
    ViewSet for ScenicArea model.
    Provides CRUD operations for scenic areas.
    """

    queryset = ScenicArea.objects.all()
    serializer_class = ScenicAreaSerializer

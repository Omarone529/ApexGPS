from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from users.permissions import IsAdminUser, IsRegisteredUser

from .models import PointOfInterest, ScenicArea
from .serializers import (
    PointOfInterestSerializer,
    ScenicAreaSerializer,
    POIPhotosResponseSerializer
)
from .services.google_places import GooglePlacesService

import logging

logger = logging.getLogger(__name__)


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.google_places = GooglePlacesService()

    @action(detail=True, methods=['get'], url_path='photos')
    def get_photos(self, request, pk=None):
        """Get photos for a specific POI using Google Places API."""
        poi = self.get_object()
        logger.info(f"Fetching photos for POI {poi.id} - {poi.name}")

        try:
            # Get photos from Google Places
            result = self.google_places.get_poi_photos(poi)

            # Add POI description for compatibility
            if poi.description:
                result['wikipedia_description'] = poi.description

            # Serialize and return
            serializer = POIPhotosResponseSerializer(result)
            return Response(serializer.data)

        except Exception as e:
            logger.error(f"Error fetching photos for POI {poi.id}: {e}", exc_info=True)
            return Response({
                'photos': [],
                'wikipedia_description': poi.description or '',
                'source': 'database',
                'configured': self.google_places.is_configured(),
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path='photos-by-coords')
    def get_photos_by_coords(self, request):
        """Finds a POI near the given coordinates and returns its photos."""
        lat = request.GET.get('lat')
        lon = request.GET.get('lon')
        name = request.GET.get('name', '')

        if not lat or not lon:
            return Response(
                {'error': 'Both lat and lon parameters are required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            lat = float(lat)
            lon = float(lon)
        except ValueError:
            return Response(
                {'error': 'Invalid lat/lon values'},
                status=status.HTTP_400_BAD_REQUEST
            )

        logger.info(f"Photo request by coords: lat={lat}, lon={lon}, name='{name}'")

        try:
            # Create point for spatial query
            point = Point(lon, lat, srid=4326)

            # Find POIs within 500 meters, ordered by distance
            pois = PointOfInterest.objects.filter(
                location__distance_lte=(point, 500),
                is_active=True
            ).annotate(
                distance=Distance('location', point)
            ).order_by('distance')

            if pois.exists():
                if name:
                    name_lower = name.lower()
                    best_match = None
                    best_score = 0

                    for poi in pois:
                        poi_name_lower = poi.name.lower()
                        if name_lower in poi_name_lower or poi_name_lower in name_lower:
                            poi_words = set(poi_name_lower.split())
                            name_words = set(name_lower.split())
                            common_words = poi_words & name_words
                            score = len(common_words)

                            # Bonus for exact match
                            if poi_name_lower == name_lower:
                                score += 10

                            if score > best_score:
                                best_score = score
                                best_match = poi

                    if best_match:
                        # Redirect to the detail photos endpoint
                        return self.get_photos(request, pk=best_match.id)

                # If no name match, take the closest POI
                closest_poi = pois.first()
                return self.get_photos(request, pk=closest_poi.id)

            # No POI found nearby
            logger.info(f"No POI found near coordinates lat={lat}, lon={lon}")
            return Response({
                'photos': [],
                'wikipedia_description': '',
                'source': 'none',
                'configured': self.google_places.is_configured(),
                'message': 'No POI found near these coordinates'
            })

        except Exception as e:
            logger.error(f"Error in photos-by-coords: {e}", exc_info=True)
            return Response({
                'photos': [],
                'wikipedia_description': '',
                'source': 'database',
                'configured': self.google_places.is_configured(),
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ScenicAreaViewSet(ProtectedResourceViewSet):
    """
    ViewSet for ScenicArea model.
    Provides CRUD operations for scenic areas.
    """

    queryset = ScenicArea.objects.all()
    serializer_class = ScenicAreaSerializer
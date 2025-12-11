from django.db import connection
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from users.permissions import IsAdminUser, IsRegisteredUser

from .models import ElevationQuery
from .serializers import ElevationQuerySerializer


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


class ElevationQueryViewSet(ProtectedResourceViewSet):
    """
    ViewSet for ElevationQuery model.
    Provides CRUD operations for elevation queries.
    """

    queryset = ElevationQuery.objects.all()
    serializer_class = ElevationQuerySerializer


class DEMViewSet(viewsets.ViewSet):
    """
    ViewSet for DEM operations.
    Provides endpoints for querying Digital Elevation Model data.
    """

    def list(self, request):
        """List available DEM endpoints and usage."""
        base_url = request.build_absolute_uri("/api/dem/dem/")
        return Response(
            {
                "endpoints": [
                    {
                        "name": "elevation",
                        "method": "GET",
                        "url": f"{base_url}elevation/",
                        "parameters": "?lat=<latitude>&lon=<longitude>",
                        "description": "Get elevation at specific coordinates",
                        "example": f"{base_url}elevation/?lat=45.5&lon=8.5",
                    },
                    {
                        "name": "statistics",
                        "method": "GET",
                        "url": f"{base_url}statistics/",
                        "description": "Get DEM statistics"
                        " (tile count, elevation range)",
                        "example": f"{base_url}statistics/",
                    },
                ],
                "description": "Digital Elevation Model (SRTM) loaded into PostGIS",
                "data_source": "NASA SRTM 30m resolution",
                "coordinate_system": "WGS84 (EPSG:4326)",
            }
        )

    @action(detail=False, methods=["get"])
    def elevation(self, request):
        """Get elevation at specific geographic coordinates."""
        lat = request.query_params.get("lat")
        lon = request.query_params.get("lon")

        if not lat or not lon:
            return Response(
                {"error": "Missing parameters. Required: lat, lon"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            lat = float(lat)
            lon = float(lon)
        except ValueError:
            return Response(
                {"error": "Invalid coordinate values. Must be numbers."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate coordinate ranges
        if not (-90 <= lat <= 90):
            return Response(
                {"error": "Latitude must be between -90 and 90 degrees"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not (-180 <= lon <= 180):
            return Response(
                {"error": "Longitude must be between -180 and 180 degrees"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Query elevation from PostGIS raster
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT ST_Value(rast, 1, ST_SetSRID(ST_Point(%s, %s), 4326))
                 as elevation
                FROM dem
                WHERE ST_Intersects(rast, ST_SetSRID(ST_Point(%s, %s), 4326))
                LIMIT 1;
            """,
                [lon, lat, lon, lat],
            )

            result = cursor.fetchone()
            elevation = float(result[0]) if result and result[0] is not None else None

        # Log the query in database
        ElevationQuery.objects.create(
            name=f"API Query {lat:.4f},{lon:.4f}",
            latitude=lat,
            longitude=lon,
            elevation=elevation,
            success=elevation is not None,
        )

        return Response(
            {
                "latitude": lat,
                "longitude": lon,
                "elevation": elevation,
                "success": elevation is not None,
                "units": "meters",
                "coordinate_system": "WGS84 (EPSG:4326)",
            }
        )

    @action(detail=False, methods=["get"])
    def statistics(self, request):
        """Get statistics about the loaded DEM data."""
        with connection.cursor() as cursor:
            # Get tile count
            cursor.execute("SELECT COUNT(*) as tile_count FROM dem;")
            tile_count = cursor.fetchone()[0]

            # Get elevation statistics
            cursor.execute(
                """
                SELECT
                    MIN((ST_SummaryStats(rast)).min) as min_elevation,
                    MAX((ST_SummaryStats(rast)).max) as max_elevation,
                    AVG((ST_SummaryStats(rast)).mean) as mean_elevation
                FROM dem;
            """
            )
            elevation_stats = cursor.fetchone()

            # Get approximate coverage area
            cursor.execute(
                """
                SELECT ST_AsText(ST_Extent(ST_ConvexHull(rast))) as coverage
                FROM dem
                LIMIT 1;
            """
            )
            coverage = cursor.fetchone()

        min_elev = float(elevation_stats[0]) if elevation_stats[0] else None
        max_elev = float(elevation_stats[1]) if elevation_stats[1] else None
        mean_elev = float(elevation_stats[2]) if elevation_stats[2] else None

        return Response(
            {
                "tiles": {
                    "count": tile_count,
                    "description": "Number of raster tiles loaded",
                },
                "elevation": {
                    "min": min_elev,
                    "max": max_elev,
                    "mean": mean_elev,
                    "range": max_elev - min_elev if min_elev and max_elev else None,
                    "units": "meters",
                },
                "coverage": {
                    "bounds": coverage[0] if coverage else None,
                    "coordinate_system": "WGS84 (EPSG:4326)",
                },
                "data_source": "SRTM 30m Digital Elevation Model",
            }
        )

    @action(detail=False, methods=["get"])
    def coverage(self, request):
        """Check if coordinates are within DEM coverage area."""
        lat = request.query_params.get("lat")
        lon = request.query_params.get("lon")

        if not lat or not lon:
            return Response(
                {"error": "Missing parameters. Required: lat, lon"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            lat = float(lat)
            lon = float(lon)
        except ValueError:
            return Response(
                {"error": "Invalid coordinate values"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT EXISTS(
                    SELECT 1 FROM dem
                    WHERE ST_Intersects(rast, ST_SetSRID(ST_Point(%s, %s), 4326))
                ) as has_coverage;
            """,
                [lon, lat],
            )

            result = cursor.fetchone()
            has_coverage = bool(result[0]) if result else False

        return Response(
            {
                "latitude": lat,
                "longitude": lon,
                "has_coverage": has_coverage,
                "message": "Point is within DEM coverage area"
                if has_coverage
                else "Point is outside DEM coverage area",
            }
        )

import time

from django.contrib.gis.geos import Point
from django.db import models
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Route, Stop
from .permissions import IsOwnerOrReadOnly
from .serializers import (
    RouteCalculationInputSerializer,
    RouteGeoSerializer,
    RouteSerializer,
    StopSerializer,
)

try:
    from routes.services.routing.fast_routing import FastRoutingService
    from routes.services.routing.route_validator import RouteValidator
except ImportError:
    FastRoutingService = None
    RouteValidator = None

__all__ = ["RouteViewSet", "StopViewSet"]


class RouteViewSet(viewsets.ModelViewSet):
    """
    ViewSet for complete route management.
    Provides CRUD operations for routes with permission controls
    based on visibility and ownership.
    """

    serializer_class = RouteSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsOwnerOrReadOnly]
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = ["visibility", "preference"]
    search_fields = ["name", "owner__username"]
    ordering_fields = ["created_at", "distance_km", "estimated_time_min"]
    ordering = ["-created_at"]

    def get_queryset(self):
        """Get the queryset of routes based on user permissions."""
        user = self.request.user

        if user.is_staff:
            return Route.objects.all()
        if user.is_authenticated:
            # For authenticated users:
            # public routes + link-shared routes + their own routes
            return Route.objects.filter(
                models.Q(owner=user)
                | models.Q(visibility="public")
                | models.Q(visibility="link")
            )

        # Anonymous users can only see public routes, not link-shared routes)
        return Route.objects.filter(visibility="public")

    def perform_create(self, serializer):
        """Perform route creation with automatic owner assignment."""
        serializer.save(owner=self.request.user)

    @action(detail=False, methods=["get"])
    def my_routes(self, request):
        """Retrieve routes belonging to the current authenticated user."""
        routes = Route.objects.filter(owner=request.user)
        serializer = self.get_serializer(routes, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def public(self, request):
        """Retrieve only public routes."""
        routes = Route.objects.filter(visibility="public")
        serializer = self.get_serializer(routes, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], permission_classes=[permissions.AllowAny])
    def geojson(self, request):
        """Retrieve routes in GeoJSON format for map visualization."""
        queryset = self.filter_queryset(self.get_queryset())
        serializer = RouteGeoSerializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def toggle_visibility(self, request, pk=None):
        """Toggle route visibility between private and public."""
        route = self.get_object()

        if route.owner != request.user and not request.user.is_staff:
            return Response(
                {"error": "Only the route owner can change visibility."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if route.visibility == "private":
            route.visibility = "public"
        else:
            route.visibility = "private"

        route.save()
        serializer = self.get_serializer(route)
        return Response(serializer.data)

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[permissions.IsAuthenticatedOrReadOnly],
        url_path="calculate-fastest",
    )
    def calculate_fastest_route(self, request):
        """
        WARNING: This route is NOT shown to users, only used for benchmarking.

        Calculate FASTEST route between two points.
        This endpoint calculates the fastest route to establish a time baseline.
        The result is used internally for time constraints on scenic routes.
        """
        start_time = time.time()

        # Check if routing services are available
        if not FastRoutingService or not RouteValidator:
            return Response(
                {
                    "error": "Routing services not available. "
                    "Please ensure database is prepared."
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Validate input (simplified version without preference)
        simplified_data = request.data.copy()
        # Remove fields not needed for fastest route
        simplified_data.pop("preference", None)
        simplified_data.pop("max_time_increase_pct", None)
        simplified_data.pop("include_fastest", None)

        input_serializer = RouteCalculationInputSerializer(data=simplified_data)
        if not input_serializer.is_valid():
            return Response(input_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = input_serializer.validated_data

        # Extract parameters
        start_lat = validated_data["start_lat"]
        start_lon = validated_data["start_lon"]
        end_lat = validated_data["end_lat"]
        end_lon = validated_data["end_lon"]
        vertex_threshold = validated_data.get("vertex_threshold", 0.01)

        # Initialize services
        fast_service = FastRoutingService()
        validator = RouteValidator()

        # Validate route request
        validation_result = validator.full_route_validation(
            start_lat=start_lat,
            start_lon=start_lon,
            end_lat=end_lat,
            end_lon=end_lon,
            max_distance_km=1000.0,
        )

        if not validation_result["is_valid"]:
            return Response(
                {
                    "error": "Route validation failed",
                    "validation": validation_result,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            fastest_route = fast_service.calculate_fastest_route(
                start_lat=start_lat,
                start_lon=start_lon,
                end_lat=end_lat,
                end_lon=end_lon,
                vertex_threshold=vertex_threshold,
            )

            if not fastest_route:
                return Response(
                    {
                        "error": "No route found between points",
                        "validation": validation_result,
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Prepare response
            response_data = {
                "route_type": "fastest_benchmark",
                "purpose": "internal_benchmark_only",
                "warning": "This route is for benchmarking only, not shown to users",
                # Route metrics
                "total_distance_km": fastest_route["total_distance_km"],
                "total_time_minutes": fastest_route["total_time_minutes"],
                "total_distance_m": fastest_route["total_distance_m"],
                "total_time_seconds": fastest_route["total_time_seconds"],
                "segment_count": fastest_route["segment_count"],
                # For validation
                "validation": {
                    "is_valid": validation_result["is_valid"],
                    "warnings": validation_result["warnings"],
                    "start_vertex": validation_result.get("start_vertex"),
                    "end_vertex": validation_result.get("end_vertex"),
                },
                # Processing info
                "processing_time_ms": (time.time() - start_time) * 1000,
                "database_status": "real_data",
                # Include minimal info for debugging
                "start_coordinates": {"lat": start_lat, "lon": start_lon},
                "end_coordinates": {"lat": end_lat, "lon": end_lon},
            }

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {
                    "error": f"Error calculating fastest route: {str(e)}",
                    "validation": validation_result,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # stop managment actions
    @action(detail=True, methods=["post"], permission_classes=[IsOwnerOrReadOnly])
    def add_stop(self, request, pk=None):
        """Add a stop to a route."""
        route = self.get_object()

        # Check if user can add stops to this route
        if route.owner != request.user and not request.user.is_staff:
            return Response(
                {"error": "Only the route owner can add stops."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Determine the next order number
        last_stop = route.stops.order_by("-order").first()
        next_order = (last_stop.order + 1) if last_stop else 1

        # Prepare stop data
        stop_data = request.data.copy()
        stop_data["route"] = route.id
        stop_data["order"] = next_order

        # Convert location if needed
        location_data = stop_data.get("location")
        if location_data and isinstance(location_data, dict):
            lat = location_data.get("lat")
            lon = location_data.get("lon")
            if lat is not None and lon is not None:
                stop_data["location"] = Point(float(lon), float(lat))
        elif "lat" in stop_data and "lon" in stop_data:
            # Alternative: direct lat/lon in request data
            lat = stop_data.get("lat")
            lon = stop_data.get("lon")
            stop_data["location"] = Point(float(lon), float(lat))
            # Remove lat/lon from data to avoid validation errors
            stop_data.pop("lat", None)
            stop_data.pop("lon", None)

        serializer = StopSerializer(data=stop_data, context={"request": request})

        if serializer.is_valid():
            # TODO: Trigger route recalculation here
            # stop = serializer.save()
            # recalculate_route_with_stops(route.id)

            return Response(serializer.data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["get"])
    def stops(self, request, pk=None):
        """Get all stops for a route in order."""
        route = self.get_object()
        stops = route.stops.order_by("order")
        serializer = StopSerializer(stops, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], permission_classes=[IsOwnerOrReadOnly])
    def reorder_stops(self, request, pk=None):
        """Reorder stops for a route."""
        route = self.get_object()

        if route.owner != request.user and not request.user.is_staff:
            return Response(
                {"error": "Only the route owner can reorder stops."},
                status=status.HTTP_403_FORBIDDEN,
            )

        new_order = request.data.get("order", [])
        if not isinstance(new_order, list):
            return Response(
                {"error": "Order must be a list of stop IDs."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate that all stop IDs belong to this route
        stop_ids = list(route.stops.values_list("id", flat=True))
        if set(new_order) != set(stop_ids):
            return Response(
                {"error": "Invalid stop IDs provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Update order
        for order_position, stop_id in enumerate(new_order, start=1):
            Stop.objects.filter(id=stop_id, route=route).update(order=order_position)

        # TODO: Trigger route recalculation
        # recalculate_route_with_stops(route.id)

        return Response({"message": "Stops reordered successfully."})

    @action(detail=True, methods=["delete"], permission_classes=[IsOwnerOrReadOnly])
    def clear_stops(self, request, pk=None):
        """Remove all stops from a route."""
        route = self.get_object()

        if route.owner != request.user and not request.user.is_staff:
            return Response(
                {"error": "Only the route owner can clear stops."},
                status=status.HTTP_403_FORBIDDEN,
            )

        route.stops.all().delete()

        # TODO: Trigger route recalculation
        # recalculate_route_with_stops(route.id)

        return Response({"message": "All stops cleared successfully."})


class StopViewSet(viewsets.ModelViewSet):
    """ViewSet for managing stops within routes."""

    queryset = Stop.objects.all()
    serializer_class = StopSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsOwnerOrReadOnly]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["route"]
    ordering_fields = ["order", "added_at"]
    ordering = ["route", "order"]

    def get_queryset(self):
        """Return stops for routes the user can access."""
        user = self.request.user

        if user.is_staff:
            return Stop.objects.all()

        if user.is_authenticated:
            # User can see stops for their routes or public routes
            return Stop.objects.filter(
                models.Q(route__owner=user) | models.Q(route__visibility="public")
            )

        # Anonymous users can only see stops for public routes
        return Stop.objects.filter(route__visibility="public")

    def perform_create(self, serializer):
        """Set the route owner automatically and validate permissions."""
        route = serializer.validated_data["route"]

        # Check if user can add stops to this route
        if route.owner != self.request.user and not self.request.user.is_staff:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You can only add stops to your own routes.")

        # Determine order if not provided
        if (
            "order" not in serializer.validated_data
            or not serializer.validated_data["order"]
        ):
            last_stop = route.stops.order_by("-order").first()
            serializer.validated_data["order"] = (
                (last_stop.order + 1) if last_stop else 1
            )

        serializer.save()

        # TODO: Trigger route recalculation
        # recalculate_route_with_stops(route.id)

    def perform_update(self, serializer):
        """Update stop and trigger route recalculation."""
        route = serializer.validated_data.get("route", serializer.instance.route)

        # Check permissions
        if route.owner != self.request.user and not self.request.user.is_staff:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You can only modify stops in your own routes.")

        serializer.save()

        # TODO: Trigger route recalculation
        # recalculate_route_with_stops(route.id)

    def perform_destroy(self, instance):
        """Delete stop and trigger route recalculation."""
        route = instance.route

        # Check permissions
        if route.owner != self.request.user and not self.request.user.is_staff:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You can only delete stops from your own routes.")

        instance.delete()

        # TODO: Trigger route recalculation
        # route_id = instance.route.id
        # recalculate_route_with_stops(route_id)

import time

from django.contrib.gis.geos import Point
from django.db import models
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from gis_data.services.topology_service import logger
from routes.services.routing.utils import _prepare_route_response

from .models import Route, Stop
from .permissions import IsOwnerOrReadOnly
from .serializers import (
    RouteCalculationInputSerializer,
    RouteCreateSerializer,
    RouteGeoSerializer,
    RouteSaveFromCalculationSerializer,
    RouteSerializer,
    RouteUpdateSerializer,
    StopSerializer,
)
from .services.routing.route_recalculation import RouteRecalculationService

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

    def get_serializer_class(self):
        """Return appropriate serializer based on action."""
        if self.action == "create":
            return RouteCreateSerializer
        elif self.action in ["update", "partial_update"]:
            return RouteUpdateSerializer
        return RouteSerializer

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

        # Anonymous users can only see public routes, not link-shared routes
        return Route.objects.filter(visibility="public")

    def perform_create(self, serializer):
        """Perform route creation with automatic owner assignment."""
        user = self.request.user
        if user.is_authenticated:
            serializer.save(owner=user)
        else:
            # For anonymous users, save without owner
            serializer.save()

    @action(detail=False, methods=["get"])
    def my_routes(self, request):
        """Retrieve routes belonging to the current authenticated user."""
        if not request.user.is_authenticated:
            return Response(
                {"error": "Authentication required to view your routes."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
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
        permission_classes=[permissions.AllowAny],
        url_path="calculate-fastest",
    )
    def calculate_fastest_route(self, request):
        """
        Calculate fastest route between two locations.
        Accepts location names which are automatically geocoded.
        ACCESSIBLE TO ALL USERS (including anonymous).
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

        # Validate input
        input_serializer = RouteCalculationInputSerializer(data=request.data)
        if not input_serializer.is_valid():
            return Response(input_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = input_serializer.validated_data

        # Extract parameters
        start_lat = validated_data["start_lat"]
        start_lon = validated_data["start_lon"]
        end_lat = validated_data["end_lat"]
        end_lon = validated_data["end_lon"]
        vertex_threshold = validated_data.get("vertex_threshold", 0.01)
        start_location_name = validated_data["start_location_name"]
        end_location_name = validated_data["end_location_name"]

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
                    "details": validation_result,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Calculate fastest route using real OSM data
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

            start_data = {
                "name": start_location_name,
                "lat": start_lat,
                "lon": start_lon,
                "geocoded": True,
                "original_name": start_location_name,
            }

            end_data = {
                "name": end_location_name,
                "lat": end_lat,
                "lon": end_lon,
                "geocoded": True,
                "original_name": end_location_name,
            }

            processing_time = time.time() - start_time
            response_data = _prepare_route_response(
                fastest_route=fastest_route,
                start_data=start_data,
                end_data=end_data,
                validation_result=validation_result,
                processing_time=processing_time,
            )

            # Allow saving only to authenticated non-VISITOR users
            response_data["can_save"] = (
                request.user.is_authenticated
                and hasattr(request.user, "role")
                and request.user.role != "VISITOR"
            )

            # Add data for possible saving
            if response_data["can_save"]:
                response_data["calculation_data"] = {
                    "start_location": {"lat": start_lat, "lon": start_lon},
                    "end_location": {"lat": end_lat, "lon": end_lon},
                    "preference": "fast",
                    "total_distance_km": fastest_route.get("total_distance_km", 0),
                    "total_time_minutes": fastest_route.get("total_time_minutes", 0),
                    "polyline": fastest_route.get("polyline", ""),
                    "total_scenic_score": 0,  # Fast routes have no panoramic score
                }

            return Response(response_data, status=status.HTTP_200_OK)

        except ValueError as e:
            return Response(
                {
                    "error": f"Invalid input: {str(e)}",
                    "validation": validation_result,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            import traceback

            logger.error(f"Error calculating route: {str(e)}\n{traceback.format_exc()}")

            return Response(
                {
                    "error": f"Error calculating fastest route: {str(e)}",
                    "validation": validation_result,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[permissions.AllowAny],
        url_path="calculate-scenic",
    )
    def calculate_scenic_route(self, request):
        """
        Calculate scenic route between two locations.
        Accepts location names and scenic preference.
        ACCESSIBLE TO ALL USERS (including anonymous).
        """
        start_time = time.time()

        try:
            from routes.services.routing.scenic_orchestrator import (
                ScenicRouteOrchestrator,
            )
        except ImportError:
            return Response(
                {
                    "error": "Scenic routing services not available. "
                    "Please ensure scenic_routing.py exists."
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Check if routing services are available
        if not FastRoutingService or not RouteValidator:
            return Response(
                {
                    "error": "Routing services not available. "
                    "Please ensure database is prepared."
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        input_serializer = RouteCalculationInputSerializer(data=request.data)
        if not input_serializer.is_valid():
            return Response(input_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = input_serializer.validated_data

        # Extract parameters
        start_lat = validated_data["start_lat"]
        start_lon = validated_data["start_lon"]
        end_lat = validated_data["end_lat"]
        end_lon = validated_data["end_lon"]
        vertex_threshold = validated_data.get("vertex_threshold", 0.01)
        start_location_name = validated_data["start_location_name"]
        end_location_name = validated_data["end_location_name"]

        # Get scenic preference from request (default to "balanced")
        preference = request.data.get("preference", "balanced")
        valid_preferences = ["fast", "balanced", "most_winding"]
        if preference not in valid_preferences:
            return Response(
                {"error": f"Invalid preference. Must be one of: {valid_preferences}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Initialize services
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
                    "details": validation_result,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            scenic_result = ScenicRouteOrchestrator.calculate_from_coordinates(
                start_lat=start_lat,
                start_lon=start_lon,
                end_lat=end_lat,
                end_lon=end_lon,
                preference=preference,
                vertex_threshold=vertex_threshold,
            )

            if not scenic_result.get("success"):
                error_msg = scenic_result.get(
                    "error", "Unknown error calculating scenic route"
                )
                return Response(
                    {
                        "error": error_msg,
                        "validation": validation_result,
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            # Prepare response data
            processing_time = time.time() - start_time

            # Add location names to result
            scenic_result["locations"] = {
                "start": {
                    "name": start_location_name,
                    "lat": start_lat,
                    "lon": start_lon,
                },
                "end": {
                    "name": end_location_name,
                    "lat": end_lat,
                    "lon": end_lon,
                },
            }

            # Add processing time
            scenic_result["processing_time_ms"] = round(processing_time * 1000, 2)
            scenic_result["can_save"] = (
                request.user.is_authenticated
                and hasattr(request.user, "role")
                and request.user.role != "VISITOR"
            )

            # Data for possible saving
            if scenic_result.get("can_save") and scenic_result.get("scenic_route"):
                scenic_route_data = scenic_result["scenic_route"]
                scenic_result["calculation_data"] = {
                    "start_location": {"lat": start_lat, "lon": start_lon},
                    "end_location": {"lat": end_lat, "lon": end_lon},
                    "preference": preference,
                    "total_distance_km": scenic_route_data.get("total_distance_km", 0),
                    "total_time_minutes": scenic_route_data.get(
                        "total_time_minutes", 0
                    ),
                    "polyline": scenic_route_data.get("polyline", ""),
                    "total_scenic_score": scenic_route_data.get("scenic_score", 0),
                    "avg_scenic_rating": scenic_route_data.get("avg_scenic_rating", 0),
                    "avg_curvature": scenic_route_data.get("avg_curvature", 0),
                    "total_poi_density": scenic_route_data.get("total_poi_density", 0),
                    "poi_count": scenic_route_data.get("poi_count", 0),
                }

            return Response(scenic_result, status=status.HTTP_200_OK)

        except ValueError as e:
            return Response(
                {
                    "error": f"Invalid input: {str(e)}",
                    "validation": validation_result,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            import traceback

            logger.error(
                f"Error calculating scenic route: {str(e)}\n{traceback.format_exc()}"
            )

            return Response(
                {
                    "error": f"Error calculating scenic route: {str(e)}",
                    "validation": validation_result,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[permissions.IsAuthenticated],  # Solo utenti autenticati
        url_path="save-calculated",
    )
    def save_calculated_route(self, request):
        """Save a previously calculated route (requires authentication)."""
        if hasattr(request.user, "role") and request.user.role == "VISITOR":
            return Response(
                {
                    "error": "Utenti VISITOR non possono salvare percorsi."
                    " Registrati per salvare."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = RouteSaveFromCalculationSerializer(
            data=request.data, context={"request": request}
        )

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            route = serializer.save()
            route_serializer = RouteSerializer(route, context={"request": request})

            return Response(
                {
                    "success": True,
                    "message": "Percorso salvato con successo",
                    "route": route_serializer.data,
                },
                status=status.HTTP_201_CREATED,
            )

        except Exception as e:
            logger.error(f"Error saving calculated route: {str(e)}")
            return Response(
                {"error": f"Errore durante il salvataggio: {str(e)}", "detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["post"], permission_classes=[IsOwnerOrReadOnly])
    def add_stop(self, request, pk=None):
        """Add a stop to a route and recalculate the route."""
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
            serializer.save()
            # Trigger route recalculation
            recalculation_result = (
                RouteRecalculationService.recalculate_route_with_stops(route.id)
            )

            # Refresh route data after recalculation
            route.refresh_from_db()

            # Get updated route data
            route_serializer = self.get_serializer(route)
            response_data = {
                "stop": serializer.data,
                "route_updated": route_serializer.data,
                "recalculation_success": recalculation_result,
                "message": "Stop added successfully"
                + (" and route recalculated" if recalculation_result else ""),
            }

            return Response(response_data, status=status.HTTP_201_CREATED)
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
        """Reorder stops for a route and recalculate."""
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

        # Trigger route recalculation
        recalculation_result = RouteRecalculationService.recalculate_route_with_stops(
            route.id
        )
        route.refresh_from_db()

        # Get updated route data
        route_serializer = self.get_serializer(route)
        response_data = {
            "message": "Stops reordered successfully.",
            "route_updated": route_serializer.data,
            "recalculation_success": recalculation_result,
        }

        return Response(response_data)

    @action(detail=True, methods=["delete"], permission_classes=[IsOwnerOrReadOnly])
    def clear_stops(self, request, pk=None):
        """Remove all stops from a route and recalculate."""
        route = self.get_object()

        if route.owner != request.user and not request.user.is_staff:
            return Response(
                {"error": "Only the route owner can clear stops."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Get count before deletion
        stop_count = route.stops.count()

        # Delete all stops
        route.stops.all().delete()

        # Trigger route recalculation
        recalculation_result = RouteRecalculationService.recalculate_route_with_stops(
            route.id
        )
        route.refresh_from_db()

        # Get updated route data
        route_serializer = self.get_serializer(route)
        response_data = {
            "message": f"All stops ({stop_count}) cleared successfully.",
            "route_updated": route_serializer.data,
            "recalculation_success": recalculation_result,
        }

        return Response(response_data)

    @action(detail=True, methods=["post"], permission_classes=[IsOwnerOrReadOnly])
    def recalculate(self, request, pk=None):
        """
        Manually trigger route recalculation.
        Useful when route needs to be updated without modifying stops.
        """
        route = self.get_object()

        if route.owner != request.user and not request.user.is_staff:
            return Response(
                {"error": "Only the route owner can recalculate the route."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Get detailed recalculation info
        recalculation_info = RouteRecalculationService.get_detailed_recalculation(
            route.id
        )

        if recalculation_info.get("success"):
            # Refresh route data
            route.refresh_from_db()
            route_serializer = self.get_serializer(route)

            return Response(
                {
                    "message": "Route recalculated successfully",
                    "recalculation_details": recalculation_info,
                    "route_updated": route_serializer.data,
                }
            )
        else:
            return Response(
                {
                    "error": "Route recalculation failed",
                    "details": recalculation_info,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


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

        # Trigger route recalculation
        RouteRecalculationService.recalculate_route_with_stops(route.id)

    def perform_update(self, serializer):
        """Update stop and trigger route recalculation."""
        route = serializer.validated_data.get("route", serializer.instance.route)

        # Check permissions
        if route.owner != self.request.user and not self.request.user.is_staff:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You can only modify stops in your own routes.")

        serializer.save()

        # Trigger route recalculation
        RouteRecalculationService.recalculate_route_with_stops(route.id)

    def perform_destroy(self, instance):
        """Delete stop and trigger route recalculation."""
        route = instance.route

        # Check permissions
        if route.owner != self.request.user and not self.request.user.is_staff:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You can only delete stops from your own routes.")

        route_id = route.id
        instance.delete()

        # Trigger route recalculation
        RouteRecalculationService.recalculate_route_with_stops(route_id)

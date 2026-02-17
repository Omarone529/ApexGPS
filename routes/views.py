import os
import time
import requests
from django.contrib.gis.geos import Point
from django.db import models
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from gis_data.services.topology_service import logger
from routes.services.routing.utils import _prepare_route_response

from .models import Route, Stop
from .permissions import IsOwnerOrReadOnly
from .serializers import (
    POIPhotoResponseSerializer,
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
        Calculate fastest route between two locations with optional waypoints.
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
        geocoded_waypoints = validated_data.get("geocoded_waypoints", [])
        vertex_threshold = validated_data.get("vertex_threshold", 0.01)
        start_location_name = validated_data["start_location_name"]
        end_location_name = validated_data["end_location_name"]

        # Check straight-line distance for start-end
        lat_diff = abs(start_lat - end_lat) * 111  # 1 grado lat = ~111 km
        lon_diff = abs(start_lon - end_lon) * 111 * 0.6
        straight_distance_km = (lat_diff ** 2 + lon_diff ** 2) ** 0.5

        if straight_distance_km < 1.0 and len(geocoded_waypoints) == 0:
            return Response(
                {
                    "error": f"I punti di partenza e arrivo sono troppo vicini"
                             f" ({straight_distance_km:.2f} km).",
                    "details": {
                        "distance_km": round(straight_distance_km, 2),
                        "minimum_required_km": 1.0,
                        "suggestion": "Inserisci località più distanti "
                                      "per un percorso significativo.",
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        logger.info(
            f"Fastest route request: {start_location_name} to {end_location_name}, "
            f"waypoints: {len(geocoded_waypoints)}"
        )

        # Initialize services
        fast_service = FastRoutingService()
        validator = RouteValidator()

        # Validate all points
        all_points = [(start_lat, start_lon)] + \
                     [(wp["lat"], wp["lon"]) for wp in geocoded_waypoints] + \
                     [(end_lat, end_lon)]

        for i, (lat, lon) in enumerate(all_points):
            point_type = "start" if i == 0 else "end" if i == len(all_points) - 1 else f"waypoint {i}"

            validation_result = validator.full_route_validation(
                start_lat=lat,
                start_lon=lon,
                end_lat=lat,  # Same point for single-point validation
                end_lon=lon,
                max_distance_km=1000.0,
            )

            if not validation_result["is_valid"]:
                return Response(
                    {
                        "error": f"Validation failed for {point_type}",
                        "details": validation_result,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            # If there are waypoints, calculate multi-segment route
            if geocoded_waypoints:
                # Build list of all points in order
                points = [(start_lat, start_lon)]
                for wp in geocoded_waypoints:
                    points.append((wp["lat"], wp["lon"]))
                points.append((end_lat, end_lon))

                # Calculate each segment
                all_segments = []
                total_distance_km = 0
                total_time_minutes = 0
                all_polylines = []

                for i in range(len(points) - 1):
                    segment_result = fast_service.calculate_fastest_route(
                        start_lat=points[i][0],
                        start_lon=points[i][1],
                        end_lat=points[i + 1][0],
                        end_lon=points[i + 1][1],
                        vertex_threshold=vertex_threshold,
                    )

                    if not segment_result:
                        return Response(
                            {
                                "error": f"Could not find route for segment {i + 1} "
                                         f"({i + 1} of {len(points) - 1})",
                            },
                            status=status.HTTP_404_NOT_FOUND,
                        )

                    all_segments.append(segment_result)
                    total_distance_km += segment_result.get("total_distance_km", 0)
                    total_time_minutes += segment_result.get("total_time_minutes", 0)
                    if segment_result.get("polyline"):
                        all_polylines.append(segment_result["polyline"])

                # Combine results
                fastest_route = {
                    "total_distance_km": total_distance_km,
                    "total_time_minutes": total_time_minutes,
                    "segment_count": len(all_segments),
                    "segments": all_segments,
                    "polyline": "|".join(all_polylines) if all_polylines else "",
                }
            else:
                # Simple route without waypoints
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
                        "error": "No route found between points. Possible reasons:\n"
                                 "1) Points are too close to each other\n"
                                 "2) Road network not available in the area\n"
                                 "3) Database connection issues",
                        "distance_km": round(straight_distance_km, 2),
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

            route_distance_km = fastest_route.get("total_distance_km", 0)
            if route_distance_km < 0.1:  # Meno di 100 metri
                logger.warning(f"Route distance too small: {route_distance_km} km")
                return Response(
                    {
                        "error": f"Il percorso calcolato è troppo breve"
                                 f" ({route_distance_km:.2f} km). "
                                 "Assicurati che le località siano sufficientemente distanti.",
                        "details": {
                            "calculated_distance_km": route_distance_km,
                            "straight_line_distance_km": round(straight_distance_km, 2),
                            "suggestion": "Prova con località più distanti"
                                          " o verifica l'input.",
                        },
                    },
                    status=status.HTTP_400_BAD_REQUEST,
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

            waypoints_data = []
            for i, wp in enumerate(geocoded_waypoints):
                waypoints_data.append({
                    "order": i + 1,
                    "name": wp["name"],
                    "original_name": wp["original_name"],
                    "lat": wp["lat"],
                    "lon": wp["lon"],
                    "geocoded": True,
                })

            processing_time = time.time() - start_time
            response_data = _prepare_route_response(
                fastest_route=fastest_route,
                start_data=start_data,
                end_data=end_data,
                validation_result={"is_valid": True, "warnings": []},
                processing_time=processing_time,
            )

            # Add waypoints to response
            response_data["waypoints"] = waypoints_data
            response_data["has_waypoints"] = len(waypoints_data) > 0

            # Distance info
            response_data["distance_info"] = {
                "straight_line_km": round(straight_distance_km, 2),
                "route_km": route_distance_km,
                "detour_factor": round(route_distance_km / straight_distance_km, 2)
                if straight_distance_km > 0
                else 1.0,
            }

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
                    "waypoints": [
                        {"lat": wp["lat"], "lon": wp["lon"], "name": wp["name"]}
                        for wp in geocoded_waypoints
                    ],
                    "preference": "fast",
                    "total_distance_km": fastest_route.get("total_distance_km", 0),
                    "total_time_minutes": fastest_route.get("total_time_minutes", 0),
                    "polyline": fastest_route.get("polyline", ""),
                    "total_scenic_score": 0,  # Fast routes have no panoramic score
                }

            logger.info(
                f"Fastest route calculated successfully: {route_distance_km:.2f} km, "
                f"{fastest_route.get('total_time_minutes', 0):.1f} min, "
                f"waypoints: {len(waypoints_data)}"
            )

            return Response(response_data, status=status.HTTP_200_OK)

        except ValueError as e:
            return Response(
                {
                    "error": f"Invalid input: {str(e)}",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            import traceback

            logger.error(f"Error calculating route: {str(e)}\n{traceback.format_exc()}")

            return Response(
                {
                    "error": f"Error calculating fastest route: {str(e)}",
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

        lat_diff = abs(start_lat - end_lat) * 111
        lon_diff = abs(start_lon - end_lon) * 111 * 0.6
        straight_distance_km = (lat_diff ** 2 + lon_diff ** 2) ** 0.5

        if straight_distance_km < 1.0:
            return Response(
                {
                    "error": f"I punti di partenza e arrivo sono "
                             f"troppo vicini ({straight_distance_km:.2f} km).",
                    "details": {
                        "distance_km": round(straight_distance_km, 2),
                        "minimum_required_km": 1.0,
                        "suggestion": "Per un percorso panoramico significativo,"
                                      " inserisci località più distanti.",
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

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

            if not scenic_result:
                logger.error("ScenicRouteOrchestrator returned None")
                return Response(
                    {
                        "error": "Scenic route calculation returned empty result",
                        "validation": validation_result,
                        "distance_km": round(straight_distance_km, 2),
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            if not scenic_result.get("success", False):
                error_msg = scenic_result.get(
                    "error", "Unknown error calculating scenic route"
                )

                if (
                        "troppo vicini" in error_msg.lower()
                        or "too close" in error_msg.lower()
                ):
                    error_msg = (
                        f"I punti sono troppo vicini per un percorso panoramico"
                        f" ({straight_distance_km:.2f} km). "
                        "Prova con località più distanti."
                    )

                return Response(
                    {
                        "error": error_msg,
                        "validation": validation_result,
                        "details": scenic_result.get("error_details", {}),
                        "distance_km": round(straight_distance_km, 2),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            scenic_route_data = scenic_result.get("scenic_route", {})
            if not scenic_route_data:
                logger.warning("Scenic route data is empty, using fallback")
                return Response(
                    {
                        "error": "Il percorso panoramico non contiene dati validi",
                        "suggestion": "Prova con un'altra coppia"
                                      " di località o cambia preferenza",
                        "distance_km": round(straight_distance_km, 2),
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            scenic_distance_km = scenic_route_data.get("total_distance_km", 0)
            if scenic_distance_km < 0.1:
                logger.warning(
                    f"Scenic route distance too small: {scenic_distance_km} km"
                )
                return Response(
                    {
                        "error": f"Il percorso panoramico calcolato è troppo breve"
                                 f" ({scenic_distance_km:.2f} km).",
                        "details": {
                            "calculated_distance_km": scenic_distance_km,
                            "straight_line_distance_km": round(straight_distance_km, 2),
                            "suggestion": "Le località potrebbero essere"
                                          " troppo vicine per un percorso panoramico.",
                        },
                    },
                    status=status.HTTP_400_BAD_REQUEST,
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

            # Add distance information
            scenic_result["distance_info"] = {
                "straight_line_km": round(straight_distance_km, 2),
                "scenic_route_km": scenic_distance_km,
                "fastest_route_km": scenic_result.get("fastest_route", {}).get(
                    "total_distance_km", 0
                ),
                "detour_factor": round(scenic_distance_km / straight_distance_km, 2)
                if straight_distance_km > 0
                else 1.0,
            }

            # Add processing time
            scenic_result["processing_time_ms"] = round(processing_time * 1000, 2)
            scenic_result["can_save"] = (
                    request.user.is_authenticated
                    and hasattr(request.user, "role")
                    and request.user.role != "VISITOR"
            )

            # Data for possible saving
            if scenic_result.get("can_save") and scenic_route_data:
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

            # Log del successo
            logger.info(
                f"Scenic route calculated successfully: "
                f"{scenic_distance_km:.2f} km, "
                f"{scenic_route_data.get('total_time_minutes', 0):.1f} min, "
                f"score: {scenic_route_data.get('scenic_score', 0):.1f}/100, "
                f"POIs: {scenic_route_data.get('poi_count', 0)}"
            )

            return Response(scenic_result, status=status.HTTP_200_OK)

        except ValueError as e:
            return Response(
                {
                    "error": f"Invalid input: {str(e)}",
                    "validation": validation_result,
                    "distance_km": round(straight_distance_km, 2),
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
                    "distance_km": round(straight_distance_km, 2),
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

@api_view(['GET'])
@permission_classes([AllowAny])
def geocode_search(request):
    """
    Search for locations by name.
    Returns list of matches with coordinates and display names.
    """
    query = request.GET.get('q', '').strip()
    limit = int(request.GET.get('limit', 5))

    if len(query) < 2:
        return Response([])

    try:
        from .services.geocoding import GeocodingService
        from .serializers import GeocodeSearchResultSerializer

        base_url = GeocodingService._get_nominatim_url()

        params = {
            "q": query,
            "format": "json",
            "limit": limit,
            "countrycodes": "it",
            "accept-language": "it",
            "addressdetails": 1,
        }

        headers = {"User-Agent": "ApexGPS/1.0"}
        search_url = f"{base_url}/search"

        response = requests.get(search_url, params=params, headers=headers, timeout=15)

        if response.status_code != 200:
            return Response([])

        data = response.json()
        results = []

        for item in data:
            # Generate a stable ID
            item_id = f"osm_{item.get('osm_id', '')}_{item.get('osm_type', 'node')}"

            # Determine location type
            location_type = item.get('type', 'location')
            if 'address' in item:
                if 'city' in item['address']:
                    location_type = 'city'
                elif 'town' in item['address']:
                    location_type = 'town'
                elif 'village' in item['address']:
                    location_type = 'village'
                elif 'hamlet' in item['address']:
                    location_type = 'hamlet'

            results.append({
                'id': item_id,
                'display_name': item.get('display_name', ''),
                'lat': float(item['lat']),
                'lon': float(item['lon']),
                'type': location_type,
                'importance': item.get('importance', 0.5),
            })

        # Sort by importance
        results.sort(key=lambda x: x['importance'], reverse=True)

        # Usa il serializer per la risposta
        serializer = GeocodeSearchResultSerializer(results, many=True)
        return Response(serializer.data)

    except Exception as e:
        logger.error(f"Geocode search error: {str(e)}")
        return Response([])


@api_view(['GET'])
@permission_classes([AllowAny])
def poi_photos(request):
    """
    Get photos for a POI from multiple sources.
    Filters out portraits/people photos, keeps only location-appropriate images.
    """
    try:
        name = request.GET.get('name', '')
        lat = request.GET.get('lat')
        lon = request.GET.get('lon')
        category = request.GET.get('category', '')

        if not lat or not lon:
            logger.warning(f"Missing coordinates for {name}")
            return Response({"photos": [], "wikipedia_description": ""})

        logger.info(f"Searching photos for {name} at {lat}, {lon}")

        # Environment variables
        wikimedia_url = os.environ.get('WIKIMEDIA_API_URL', 'https://commons.wikimedia.org/w/api.php')
        wikipedia_url = os.environ.get('WIKIPEDIA_API_URL', 'https://it.wikipedia.org/w/api.php')
        pic4carto_base = os.environ.get('PIC4CARTO_API_URL', 'https://api-pic4carto.openstreetmap.fr')
        pic4carto_url = f"{pic4carto_base}/search/around"
        user_agent = os.environ.get('API_USER_AGENT', 'ApexGPS/1.0 (https://apexgps.com)')

        headers = {'User-Agent': user_agent}
        photos = []

        # Keywords to exclude portraits and people photos
        exclude_keywords = [
            'portrait', 'ritratto', 'selfie', 'foto di gruppo',
            'person', 'persona', 'people', 'gente', 'uomo', 'donna',
            'man', 'woman', 'child', 'bambino', 'family', 'famiglia',
            'viso', 'face', 'profile', 'profilo', 'autoritratto',
            'wedding', 'matrimonio', 'party', 'festa', 'group', 'gruppo',
            'tourist', 'turista', 'visitor', 'visitatore', 'crowd', 'folla',
            'ritratto fotografico', 'photographic portrait', 'headshot'
        ]

        # Keywords to include location-appropriate photos
        place_keywords = [
            'church', 'chiesa', 'cathedral', 'duomo', 'basilica',
            'castle', 'castello', 'fortress', 'fortezza',
            'monument', 'monumento', 'statua', 'statue',
            'museum', 'museo', 'gallery', 'galleria',
            'view', 'vista', 'panorama', 'panoramic',
            'lake', 'lago', 'river', 'fiume', 'waterfall', 'cascata',
            'mountain', 'montagna', 'hill', 'colle', 'pass', 'passo',
            'vineyard', 'vigneto', 'wine', 'vino',
            'square', 'piazza', 'street', 'via', 'road', 'strada',
            'building', 'edificio', 'palace', 'palazzo',
            'park', 'parco', 'garden', 'giardino',
            'bridge', 'ponte', 'tower', 'torre',
            'ruins', 'rovine', 'archaeological', 'archeologico',
            'fountain', 'fontana', 'well', 'pozzo',
            'coast', 'costa', 'sea', 'mare', 'beach', 'spiaggia',
            'valley', 'valle', 'cliff', 'scogliera',
            'restaurant', 'ristorante', 'trattoria', 'osteria', 'taverna', 'locanda',
            'food', 'cibo', 'pizza', 'pizzeria', 'eating', 'mangiare',
            'cafe', 'caffè', 'coffee', 'bar', 'pub', 'brewery', 'birreria',
            'wine bar', 'enoteca', 'gelateria', 'ice cream', 'pastry', 'pasticceria',
            'bakery', 'forno', 'meal', 'pranzo', 'cena', 'dinner', 'lunch',
        ]

        def is_relevant_photo(title, description=""):
            """Check if photo is location-appropriate (not a portrait)."""
            text = f"{title} {description}".lower()

            for keyword in exclude_keywords:
                if keyword in text:
                    return False

            for keyword in place_keywords:
                if keyword in text:
                    return True

            return True

        # Wikimedia Commons geosearch (most accurate)
        params_geo = {
            "action": "query",
            "format": "json",
            "list": "geosearch",
            "gscoord": f"{lat}|{lon}",
            "gsradius": "200",
            "gslimit": "10",
            "gsnamespace": "6"
        }

        try:
            geo_response = requests.get(wikimedia_url, params=params_geo, headers=headers, timeout=10)
            geo_data = geo_response.json()

            if "query" in geo_data and "geosearch" in geo_data["query"]:
                for item in geo_data["query"]["geosearch"]:
                    title = item["title"]
                    if title.startswith("File:"):
                        title = title[5:]

                    params_info = {
                        "action": "query",
                        "format": "json",
                        "titles": f"File:{title}",
                        "prop": "imageinfo|categories",
                        "iiprop": "url|extmetadata",
                        "cllimit": 10
                    }

                    info_response = requests.get(wikimedia_url, params=params_info, headers=headers, timeout=10)
                    info_data = info_response.json()

                    pages = info_data.get("query", {}).get("pages", {})
                    for page_id, page_info in pages.items():
                        if page_id != "-1":
                            imageinfo = page_info.get("imageinfo", [])
                            categories = page_info.get("categories", [])
                            category_text = " ".join([cat.get("title", "") for cat in categories])

                            if imageinfo:
                                image_data = imageinfo[0]
                                extmetadata = image_data.get("extmetadata", {})
                                description = extmetadata.get("ImageDescription", {}).get("value", "")

                                if not is_relevant_photo(title, description + " " + category_text):
                                    continue

                                date = extmetadata.get("DateTimeOriginal", {}).get("value", "")
                                if not date:
                                    date = extmetadata.get("DateTime", {}).get("value", "")

                                if date:
                                    if "T" in date:
                                        date = date.split("T")[0]
                                    elif len(date) > 10:
                                        date = date[:10]

                                photos.append({
                                    "id": item.get("pageid"),
                                    "url": image_data.get("url"),
                                    "thumbnail": image_data.get("thumburl", image_data.get("url")),
                                    "date": date,
                                    "source": "Wikimedia Commons"
                                })
        except Exception as e:
            logger.error(f"Wikimedia geosearch error: {e}")

        # Pic4Carto (aggregates Mapillary, Flickr, etc.)
        if len(photos) < 3:
            try:
                params_pic = {
                    "lat": lat,
                    "lng": lon,
                    "radius": 200,
                    "limit": 10
                }
                pic_response = requests.get(pic4carto_url, params=params_pic, timeout=5)

                if pic_response.ok:
                    pic_data = pic_response.json()
                    for item in pic_data:
                        title = item.get("title", "")
                        description = item.get("description", "")

                        if not is_relevant_photo(title, description):
                            continue

                        date_taken = item.get("date_taken", "")
                        if date_taken and "T" in date_taken:
                            date_taken = date_taken.split("T")[0]

                        photos.append({
                            "id": item.get("id"),
                            "url": item.get("url"),
                            "thumbnail": item.get("thumbnail_url", item.get("url")),
                            "date": date_taken,
                            "source": item.get("provider", "Pic4Carto")
                        })
            except Exception as e:
                logger.error(f"Pic4Carto error: {e}")

        # Wikimedia text search
        if len(photos) < 2:
            try:
                search_query = name
                if category:
                    category_map = {
                        'church': 'chiesa',
                        'restaurant': 'ristorante',
                        'food': 'ristorante',
                        'castle': 'castello',
                        'monument': 'monumento',
                        'lake': 'lago',
                        'mountain_pass': 'passo',
                        'waterfall': 'cascata',
                        'vineyard': 'vigneto',
                        'historic': 'storico',
                        'viewpoint': 'belvedere',
                        'museum': 'museo',
                        'archaeological': 'scavi archeologici',
                    }
                    if category in category_map:
                        search_query = f"{name} {category_map[category]}"

                params_search = {
                    "action": "query",
                    "format": "json",
                    "list": "search",
                    "srsearch": search_query,
                    "srnamespace": "6",
                    "srlimit": "5"
                }

                search_response = requests.get(wikimedia_url, params=params_search, headers=headers, timeout=10)
                search_data = search_response.json()

                if "query" in search_data and "search" in search_data["query"]:
                    for item in search_data["query"]["search"]:
                        title = item["title"]
                        if title.startswith("File:"):
                            title = title[5:]

                        params_info = {
                            "action": "query",
                            "format": "json",
                            "titles": f"File:{title}",
                            "prop": "imageinfo|categories",
                            "iiprop": "url|extmetadata",
                            "cllimit": 10
                        }

                        info_response = requests.get(wikimedia_url, params=params_info, headers=headers, timeout=10)
                        info_data = info_response.json()

                        pages = info_data.get("query", {}).get("pages", {})
                        for page_id, page_info in pages.items():
                            if page_id != "-1":
                                imageinfo = page_info.get("imageinfo", [])
                                categories = page_info.get("categories", [])
                                category_text = " ".join([cat.get("title", "") for cat in categories])

                                if imageinfo:
                                    image_data = imageinfo[0]
                                    extmetadata = image_data.get("extmetadata", {})
                                    description = extmetadata.get("ImageDescription", {}).get("value", "")

                                    if not is_relevant_photo(title, description + " " + category_text):
                                        continue

                                    date = extmetadata.get("DateTimeOriginal", {}).get("value", "")
                                    if not date:
                                        date = extmetadata.get("DateTime", {}).get("value", "")

                                    if date and "T" in date:
                                        date = date.split("T")[0]
                                    elif date and len(date) > 10:
                                        date = date[:10]

                                    photos.append({
                                        "id": item.get("pageid"),
                                        "url": image_data.get("url"),
                                        "thumbnail": image_data.get("thumburl", image_data.get("url")),
                                        "date": date,
                                        "source": "Wikimedia Commons"
                                    })
            except Exception as e:
                logger.error(f"Wikimedia text search error: {e}")

        # Wikipedia description (when no photos are available)
        wikipedia_description = ""
        try:
            params_wiki = {
                "action": "query",
                "format": "json",
                "list": "geosearch",
                "gscoord": f"{lat}|{lon}",
                "gsradius": "500",
                "gslimit": "1"
            }

            wiki_response = requests.get(wikipedia_url, params=params_wiki, headers=headers, timeout=5)
            wiki_data = wiki_response.json()

            if "query" in wiki_data and "geosearch" in wiki_data["query"]:
                for item in wiki_data["query"]["geosearch"]:
                    page_title = item["title"]

                    params_extract = {
                        "action": "query",
                        "format": "json",
                        "titles": page_title,
                        "prop": "extracts",
                        "exintro": True,
                        "explaintext": True,
                        "exchars": 300
                    }

                    extract_response = requests.get(wikipedia_url, params=params_extract, headers=headers, timeout=5)
                    extract_data = extract_response.json()

                    pages = extract_data.get("query", {}).get("pages", {})
                    for page_id, page_info in pages.items():
                        if page_id != "-1":
                            wikipedia_description = page_info.get("extract", "")
                            break
        except Exception as e:
            logger.error(f"Wikipedia error: {e}")

        # Remove duplicates by URL
        seen_urls = set()
        unique_photos = []
        for photo in photos:
            if photo["url"] not in seen_urls:
                seen_urls.add(photo["url"])
                unique_photos.append(photo)

        logger.info(f"Found {len(unique_photos)} relevant photos for {name} after filtering")

        response_data = {
            "photos": unique_photos[:5],
            "wikipedia_description": wikipedia_description
        }

        serializer = POIPhotoResponseSerializer(response_data)
        return Response(serializer.data)

    except Exception as e:
        logger.error(f"Error fetching photos: {e}")
        import traceback
        traceback.print_exc()
        return Response({"photos": [], "wikipedia_description": ""})
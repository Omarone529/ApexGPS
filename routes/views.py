import os
import time
import traceback

import requests
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.db import models
from django_filters.rest_framework import DjangoFilterBackend
from django.utils import timezone
from django.db.models import Q
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response

from gis_data.services.topology_service import logger
from routes.services.routing.utils import (
    _fetch_pic4carto,
    _fetch_wikipedia_description,
    _fetch_wikipedia_image,
    _fetch_wikimedia_geosearch,
    _prepare_route_response, _check_route_ownership, _routing_services_unavailable, _compute_straight_distance_km,
)

from .models import Route, Stop
from .permissions import IsOwnerOrReadOnly
from .serializers import (
    GeocodeSearchResultSerializer,
    POIPhotoResponseSerializer,
    RouteCalculationInputSerializer,
    RouteCreateSerializer,
    RouteGeoSerializer,
    RouteSaveFromCalculationSerializer,
    RouteSerializer,
    RouteUpdateSerializer,
    StopSerializer,
)
from .services.geocoding import GeocodingService
from .services.routing.route_recalculation import RouteRecalculationService
from .serializers import HiddenUntilSerializer

try:
    from routes.services.routing.fast_routing import FastRoutingService
    from routes.services.routing.route_validator import RouteValidator
except ImportError:
    FastRoutingService = None
    RouteValidator = None

_all_ = ["RouteViewSet", "StopViewSet"]


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
        if self.action in ["update", "partial_update"]:
            return RouteUpdateSerializer
        return RouteSerializer

    def get_queryset(self):
        """Get the queryset of routes based on user permissions."""
        user = self.request.user
        now = timezone.now()

        if user.is_staff:
            return Route.objects.all()

        if user.is_authenticated:
            return Route.objects.filter(
                Q(owner=user) |
                (
                        Q(visibility='public') &
                        (Q(owner__hiddenUntil__isnull=True) | Q(owner__hiddenUntil__lte=now))
                )
            )
        else:
            # Anonimo: solo percorsi pubblici e non nascosti
            return Route.objects.filter(
                visibility='public',
                owner__hiddenUntil__isnull=True
            ) | Route.objects.filter(
                visibility='public',
                owner__hiddenUntil__lte=now
            )



    def perform_create(self, serializer):
        """Perform route creation with automatic owner assignment."""
        user = self.request.user
        if user.is_authenticated:
            serializer.save(owner=user)
        else:
            serializer.save()

    # ------------------------------------------------------------------
    # Custom actions
    # ------------------------------------------------------------------


    @action(detail=True, methods=['post'], permission_classes=[IsAdminUser])
    def ban(self, request, pk=None):
        """Imposta la data di hiddenUntil per il percorso (solo admin)."""
        route = self.get_object()
        serializer = HiddenUntilSerializer(data=request.data)
        if serializer.is_valid():
            route.hiddenUntil = serializer.validated_data.get('hidden_until')
            route.save(update_fields=['hiddenUntil'])
            return Response({
                'status': 'hidden_until aggiornato',
                'hidden_until': route.hiddenUntil
            })
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['delete'], permission_classes=[IsAdminUser])
    def unban(self, request, pk=None):
        """Rimuove il blocco (hiddenUntil = null)."""
        route = self.get_object()
        route.hiddenUntil = None
        route.save(update_fields=['hiddenUntil'])
        return Response({'status': 'hidden_until rimosso'})

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
        """Retrieve routes in GeoJSON format for map visualisation."""
        queryset = self.filter_queryset(self.get_queryset())
        serializer = RouteGeoSerializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def toggle_visibility(self, request, pk=None):
        """Toggle route visibility between private and public."""
        route = self.get_object()

        if denied := _check_route_ownership(route, request.user):
            return denied

        route.visibility = "public" if route.visibility == "private" else "private"
        route.save()
        return Response(self.get_serializer(route).data)

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[permissions.AllowAny],
        url_path="calculate-fastest",
    )
    def calculate_fastest_route(self, request):
        """
        Calculate the fastest route between two locations with optional waypoints.
        Accessible to all users (including anonymous).
        """
        start_time = time.time()

        if not FastRoutingService or not RouteValidator:
            return _routing_services_unavailable()

        input_serializer = RouteCalculationInputSerializer(data=request.data)
        if not input_serializer.is_valid():
            return Response(input_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = input_serializer.validated_data
        start_lat = validated_data["start_lat"]
        start_lon = validated_data["start_lon"]
        end_lat = validated_data["end_lat"]
        end_lon = validated_data["end_lon"]
        geocoded_waypoints = validated_data.get("geocoded_waypoints", [])
        vertex_threshold = validated_data.get("vertex_threshold", 0.01)
        start_location_name = validated_data["start_location_name"]
        end_location_name = validated_data["end_location_name"]

        straight_distance_km = _compute_straight_distance_km(start_lat, start_lon, end_lat, end_lon)

        if straight_distance_km < 1.0 and not geocoded_waypoints:
            return Response(
                {
                    "error": f"I punti di partenza e arrivo sono troppo vicini ({straight_distance_km:.2f} km).",
                    "details": {
                        "distance_km": round(straight_distance_km, 2),
                        "minimum_required_km": 1.0,
                        "suggestion": "Inserisci località più distanti per un percorso significativo.",
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        logger.info(
            f"Fastest route request: {start_location_name} → {end_location_name}, "
            f"waypoints: {len(geocoded_waypoints)}"
        )

        fast_service = FastRoutingService()
        validator = RouteValidator()

        # Validate every point individually
        all_points = (
            [(start_lat, start_lon)]
            + [(wp["lat"], wp["lon"]) for wp in geocoded_waypoints]
            + [(end_lat, end_lon)]
        )

        for i, (lat, lon) in enumerate(all_points):
            point_label = (
                "start" if i == 0
                else "end" if i == len(all_points) - 1
                else f"waypoint {i}"
            )
            validation_result = validator.full_route_validation(
                start_lat=lat, start_lon=lon,
                end_lat=lat, end_lon=lon,
                max_distance_km=1000.0,
            )
            if not validation_result["is_valid"]:
                return Response(
                    {"error": f"Validation failed for {point_label}", "details": validation_result},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            if geocoded_waypoints:
                points = (
                    [(start_lat, start_lon)]
                    + [(wp["lat"], wp["lon"]) for wp in geocoded_waypoints]
                    + [(end_lat, end_lon)]
                )
                all_segments, total_distance_km, total_time_minutes, all_polylines = [], 0, 0, []

                for i in range(len(points) - 1):
                    segment_result = fast_service.calculate_fastest_route(
                        start_lat=points[i][0], start_lon=points[i][1],
                        end_lat=points[i + 1][0], end_lon=points[i + 1][1],
                        vertex_threshold=vertex_threshold,
                    )
                    if not segment_result:
                        return Response(
                            {"error": f"Could not find route for segment {i + 1} of {len(points) - 1}"},
                            status=status.HTTP_404_NOT_FOUND,
                        )
                    all_segments.append(segment_result)
                    total_distance_km += segment_result.get("total_distance_km", 0)
                    total_time_minutes += segment_result.get("total_time_minutes", 0)
                    if segment_result.get("polyline"):
                        all_polylines.append(segment_result["polyline"])

                fastest_route = {
                    "total_distance_km": total_distance_km,
                    "total_time_minutes": total_time_minutes,
                    "segment_count": len(all_segments),
                    "segments": all_segments,
                    "polyline": "|".join(all_polylines),
                }
            else:
                fastest_route = fast_service.calculate_fastest_route(
                    start_lat=start_lat, start_lon=start_lon,
                    end_lat=end_lat, end_lon=end_lon,
                    vertex_threshold=vertex_threshold,
                )

            if not fastest_route:
                return Response(
                    {
                        "error": (
                            "No route found between points. Possible reasons:\n"
                            "1) Points are too close\n"
                            "2) Road network not available in the area\n"
                            "3) Database connection issues"
                        ),
                        "distance_km": round(straight_distance_km, 2),
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

            route_distance_km = fastest_route.get("total_distance_km", 0)
            if route_distance_km < 0.1:
                logger.warning(f"Route distance too small: {route_distance_km} km")
                return Response(
                    {
                        "error": f"Il percorso calcolato è troppo breve ({route_distance_km:.2f} km).",
                        "details": {
                            "calculated_distance_km": route_distance_km,
                            "straight_line_distance_km": round(straight_distance_km, 2),
                            "suggestion": "Prova con località più distanti o verifica l'input.",
                        },
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            start_data = {
                "name": start_location_name, "lat": start_lat, "lon": start_lon,
                "geocoded": True, "original_name": start_location_name,
            }
            end_data = {
                "name": end_location_name, "lat": end_lat, "lon": end_lon,
                "geocoded": True, "original_name": end_location_name,
            }
            waypoints_data = [
                {
                    "order": i + 1,
                    "name": wp["name"],
                    "original_name": wp["original_name"],
                    "lat": wp["lat"],
                    "lon": wp["lon"],
                    "geocoded": True,
                }
                for i, wp in enumerate(geocoded_waypoints)
            ]

            processing_time = time.time() - start_time
            response_data = _prepare_route_response(
                fastest_route=fastest_route,
                start_data=start_data,
                end_data=end_data,
                validation_result={"is_valid": True, "warnings": []},
                processing_time=processing_time,
            )

            response_data["waypoints"] = waypoints_data
            response_data["has_waypoints"] = bool(waypoints_data)
            response_data["distance_info"] = {
                "straight_line_km": round(straight_distance_km, 2),
                "route_km": route_distance_km,
                "detour_factor": (
                    round(route_distance_km / straight_distance_km, 2)
                    if straight_distance_km > 0 else 1.0
                ),
            }
            response_data["can_save"] = (
                request.user.is_authenticated
                and hasattr(request.user, "role")
                and request.user.role != "VISITOR"
            )

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
                    "total_scenic_score": 0,
                }

            logger.info(
                f"Fastest route calculated: {route_distance_km:.2f} km, "
                f"{fastest_route.get('total_time_minutes', 0):.1f} min, "
                f"waypoints: {len(waypoints_data)}"
            )
            return Response(response_data, status=status.HTTP_200_OK)

        except ValueError as e:
            return Response({"error": f"Invalid input: {e}"}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"Error calculating fastest route: {e}\n{traceback.format_exc()}")
            return Response(
                {"error": f"Error calculating fastest route: {e}"},
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
        Calculate a scenic route between two locations.
        Accessible to all users (including anonymous).
        """
        start_time = time.time()

        try:
            from routes.services.routing.scenic_orchestrator import ScenicRouteOrchestrator
        except ImportError:
            return Response(
                {
                    "error": (
                        "Scenic routing services not available. "
                        "Please ensure scenic_routing.py exists."
                    )
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if not FastRoutingService or not RouteValidator:
            return _routing_services_unavailable()

        input_serializer = RouteCalculationInputSerializer(data=request.data)
        if not input_serializer.is_valid():
            return Response(input_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = input_serializer.validated_data
        start_lat = validated_data["start_lat"]
        start_lon = validated_data["start_lon"]
        end_lat = validated_data["end_lat"]
        end_lon = validated_data["end_lon"]
        vertex_threshold = validated_data.get("vertex_threshold", 0.01)
        start_location_name = validated_data["start_location_name"]
        end_location_name = validated_data["end_location_name"]

        straight_distance_km = _compute_straight_distance_km(start_lat, start_lon, end_lat, end_lon)

        if straight_distance_km < 1.0:
            return Response(
                {
                    "error": f"I punti di partenza e arrivo sono troppo vicini ({straight_distance_km:.2f} km).",
                    "details": {
                        "distance_km": round(straight_distance_km, 2),
                        "minimum_required_km": 1.0,
                        "suggestion": "Per un percorso panoramico significativo, inserisci località più distanti.",
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        preference = request.data.get("preference", "balanced")
        valid_preferences = ["fast", "balanced", "most_winding"]
        if preference not in valid_preferences:
            return Response(
                {"error": f"Invalid preference. Must be one of: {valid_preferences}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        validator = RouteValidator()
        validation_result = validator.full_route_validation(
            start_lat=start_lat, start_lon=start_lon,
            end_lat=end_lat, end_lon=end_lon,
            max_distance_km=1000.0,
        )
        if not validation_result["is_valid"]:
            return Response(
                {"error": "Route validation failed", "details": validation_result},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            scenic_result = ScenicRouteOrchestrator.calculate_from_coordinates(
                start_lat=start_lat, start_lon=start_lon,
                end_lat=end_lat, end_lon=end_lon,
                preference=preference,
                vertex_threshold=vertex_threshold,
            )

            if not scenic_result:
                logger.error("ScenicRouteOrchestrator returned None")
                return Response(
                    {
                        "error": "Scenic route calculation returned an empty result",
                        "validation": validation_result,
                        "distance_km": round(straight_distance_km, 2),
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            if not scenic_result.get("success", False):
                error_msg = scenic_result.get("error", "Unknown error calculating scenic route")
                if "troppo vicini" in error_msg.lower() or "too close" in error_msg.lower():
                    error_msg = (
                        f"I punti sono troppo vicini per un percorso panoramico "
                        f"({straight_distance_km:.2f} km). Prova con località più distanti."
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
                logger.warning("Scenic route data is empty")
                return Response(
                    {
                        "error": "Il percorso panoramico non contiene dati validi",
                        "suggestion": "Prova con un'altra coppia di località o cambia preferenza",
                        "distance_km": round(straight_distance_km, 2),
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            scenic_distance_km = scenic_route_data.get("total_distance_km", 0)
            if scenic_distance_km < 0.1:
                logger.warning(f"Scenic route distance too small: {scenic_distance_km} km")
                return Response(
                    {
                        "error": f"Il percorso panoramico calcolato è troppo breve ({scenic_distance_km:.2f} km).",
                        "details": {
                            "calculated_distance_km": scenic_distance_km,
                            "straight_line_distance_km": round(straight_distance_km, 2),
                            "suggestion": "Le località potrebbero essere troppo vicine per un percorso panoramico.",
                        },
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            processing_time = time.time() - start_time

            scenic_result["locations"] = {
                "start": {"name": start_location_name, "lat": start_lat, "lon": start_lon},
                "end": {"name": end_location_name, "lat": end_lat, "lon": end_lon},
            }
            scenic_result["distance_info"] = {
                "straight_line_km": round(straight_distance_km, 2),
                "scenic_route_km": scenic_distance_km,
                "fastest_route_km": scenic_result.get("fastest_route", {}).get("total_distance_km", 0),
                "detour_factor": (
                    round(scenic_distance_km / straight_distance_km, 2)
                    if straight_distance_km > 0 else 1.0
                ),
            }
            scenic_result["processing_time_ms"] = round(processing_time * 1000, 2)
            scenic_result["can_save"] = (
                request.user.is_authenticated
                and hasattr(request.user, "role")
                and request.user.role != "VISITOR"
            )

            if scenic_result.get("can_save") and scenic_route_data:
                scenic_result["calculation_data"] = {
                    "start_location": {"lat": start_lat, "lon": start_lon},
                    "end_location": {"lat": end_lat, "lon": end_lon},
                    "preference": preference,
                    "total_distance_km": scenic_route_data.get("total_distance_km", 0),
                    "total_time_minutes": scenic_route_data.get("total_time_minutes", 0),
                    "polyline": scenic_route_data.get("polyline", ""),
                    "total_scenic_score": scenic_route_data.get("scenic_score", 0),
                    "avg_scenic_rating": scenic_route_data.get("avg_scenic_rating", 0),
                    "avg_curvature": scenic_route_data.get("avg_curvature", 0),
                    "total_poi_density": scenic_route_data.get("total_poi_density", 0),
                    "poi_count": scenic_route_data.get("poi_count", 0),
                }

            logger.info(
                f"Scenic route calculated: {scenic_distance_km:.2f} km, "
                f"{scenic_route_data.get('total_time_minutes', 0):.1f} min, "
                f"score: {scenic_route_data.get('scenic_score', 0):.1f}/100, "
                f"POIs: {scenic_route_data.get('poi_count', 0)}"
            )
            return Response(scenic_result, status=status.HTTP_200_OK)

        except ValueError as e:
            return Response(
                {
                    "error": f"Invalid input: {e}",
                    "validation": validation_result,
                    "distance_km": round(straight_distance_km, 2),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.error(f"Error calculating scenic route: {e}\n{traceback.format_exc()}")
            return Response(
                {
                    "error": f"Error calculating scenic route: {e}",
                    "validation": validation_result,
                    "distance_km": round(straight_distance_km, 2),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[permissions.IsAuthenticated],
        url_path="save-calculated",
    )
    def save_calculated_route(self, request):
        """Save a previously calculated route (requires authentication)."""
        if hasattr(request.user, "role") and request.user.role == "VISITOR":
            return Response(
                {"error": "Utenti VISITOR non possono salvare percorsi. Registrati per salvare."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = RouteSaveFromCalculationSerializer(
            data=request.data, context={"request": request}
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            route = serializer.save()
            return Response(
                {
                    "success": True,
                    "message": "Percorso salvato con successo",
                    "route": RouteSerializer(route, context={"request": request}).data,
                },
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            logger.error(f"Error saving calculated route: {e}")
            return Response(
                {"error": f"Errore durante il salvataggio: {e}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["post"], permission_classes=[IsOwnerOrReadOnly])
    def add_stop(self, request, pk=None):
        """Add a stop to a route and recalculate the route."""
        route = self.get_object()

        if denied := _check_route_ownership(route, request.user):
            return denied

        last_stop = route.stops.order_by("-order").first()
        next_order = (last_stop.order + 1) if last_stop else 1

        stop_data = request.data.copy()
        stop_data["route"] = route.id
        stop_data["order"] = next_order

        # Normalise location to a Point
        location_data = stop_data.get("location")
        if location_data and isinstance(location_data, dict):
            lat = location_data.get("lat")
            lon = location_data.get("lon")
            if lat is not None and lon is not None:
                stop_data["location"] = Point(float(lon), float(lat))
        elif "lat" in stop_data and "lon" in stop_data:
            stop_data["location"] = Point(float(stop_data.pop("lon")), float(stop_data.pop("lat")))

        serializer = StopSerializer(data=stop_data, context={"request": request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        serializer.save()
        recalculation_result = RouteRecalculationService.recalculate_route_with_stops(route.id)
        route.refresh_from_db()

        return Response(
            {
                "stop": serializer.data,
                "route_updated": self.get_serializer(route).data,
                "recalculation_success": recalculation_result,
                "message": "Stop added successfully"
                + (" and route recalculated" if recalculation_result else ""),
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"])
    def stops(self, request, pk=None):
        """Get all stops for a route in order."""
        route = self.get_object()
        serializer = StopSerializer(route.stops.order_by("order"), many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], permission_classes=[IsOwnerOrReadOnly])
    def reorder_stops(self, request, pk=None):
        """Reorder stops for a route and recalculate."""
        route = self.get_object()

        if denied := _check_route_ownership(route, request.user):
            return denied

        new_order = request.data.get("order", [])
        if not isinstance(new_order, list):
            return Response(
                {"error": "Order must be a list of stop IDs."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        stop_ids = set(route.stops.values_list("id", flat=True))
        if set(new_order) != stop_ids:
            return Response(
                {"error": "Invalid stop IDs provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        for position, stop_id in enumerate(new_order, start=1):
            Stop.objects.filter(id=stop_id, route=route).update(order=position)

        recalculation_result = RouteRecalculationService.recalculate_route_with_stops(route.id)
        route.refresh_from_db()

        return Response(
            {
                "message": "Stops reordered successfully.",
                "route_updated": self.get_serializer(route).data,
                "recalculation_success": recalculation_result,
            }
        )

    @action(detail=True, methods=["delete"], permission_classes=[IsOwnerOrReadOnly])
    def clear_stops(self, request, pk=None):
        """Remove all stops from a route and recalculate."""
        route = self.get_object()

        if denied := _check_route_ownership(route, request.user):
            return denied

        stop_count = route.stops.count()
        route.stops.all().delete()

        recalculation_result = RouteRecalculationService.recalculate_route_with_stops(route.id)
        route.refresh_from_db()

        return Response(
            {
                "message": f"All stops ({stop_count}) cleared successfully.",
                "route_updated": self.get_serializer(route).data,
                "recalculation_success": recalculation_result,
            }
        )

    @action(detail=True, methods=["post"], permission_classes=[IsOwnerOrReadOnly])
    def recalculate(self, request, pk=None):
        """Manually trigger route recalculation."""
        route = self.get_object()

        if denied := _check_route_ownership(route, request.user):
            return denied

        recalculation_info = RouteRecalculationService.get_detailed_recalculation(route.id)

        if recalculation_info.get("success"):
            route.refresh_from_db()
            return Response(
                {
                    "message": "Route recalculated successfully",
                    "recalculation_details": recalculation_info,
                    "route_updated": self.get_serializer(route).data,
                }
            )

        return Response(
            {"error": "Route recalculation failed", "details": recalculation_info},
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
        now = timezone.now()

        if user.is_staff:
            return Stop.objects.all()

        if user.is_authenticated:
            # Stops di percorsi:
            # - di proprietà dell'utente
            # - pubblici o con link, purché non nascosti
            return Stop.objects.filter(
                Q(route__owner=user) |
                (
                        Q(route__visibility='public') &
                        (Q(route__owner__hiddenUntil__isnull=True) | Q(route__owner__hiddenUntil__lte=now))
                )
            )
        else:
            return Stop.objects.filter(
                route__visibility='public',
                route__owner__hiddenUntil__isnull=True
            ) | Stop.objects.filter(
                route__visibility='public',
                route__owner__hiddenUntil__lte=now
            )

    def _assert_can_modify_route(self, route):
        if route.owner != self.request.user and not self.request.user.is_staff:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You can only modify stops in your own routes.")

    def perform_create(self, serializer):
        route = serializer.validated_data["route"]
        self._assert_can_modify_route(route)

        if not serializer.validated_data.get("order"):
            last_stop = route.stops.order_by("-order").first()
            serializer.validated_data["order"] = (last_stop.order + 1) if last_stop else 1

        serializer.save()
        RouteRecalculationService.recalculate_route_with_stops(route.id)

    def perform_update(self, serializer):
        route = serializer.validated_data.get("route", serializer.instance.route)
        self._assert_can_modify_route(route)
        serializer.save()
        RouteRecalculationService.recalculate_route_with_stops(route.id)

    def perform_destroy(self, instance):
        route = instance.route
        self._assert_can_modify_route(route)
        route_id = route.id
        instance.delete()
        RouteRecalculationService.recalculate_route_with_stops(route_id)

@api_view(["GET"])
@permission_classes([AllowAny])
def geocode_search(request):
    """
    Search for locations by name.
    Returns a list of matches with coordinates and display names.
    """
    query = request.GET.get("q", "").strip()
    limit = int(request.GET.get("limit", 5))

    if len(query) < 2:
        return Response([])

    try:
        base_url = GeocodingService._get_nominatim_url()
        params = {
            "q": query,
            "format": "json",
            "limit": limit,
            "countrycodes": "it",
            "accept-language": "it",
            "addressdetails": 1,
        }
        response = requests.get(
            f"{base_url}/search",
            params=params,
            headers={"User-Agent": "ApexGPS/1.0"},
            timeout=15,
        )

        if response.status_code != 200:
            return Response([])

        results = []
        for item in response.json():
            item_id = f"osm_{item.get('osm_id', '')}_{item.get('osm_type', 'node')}"

            location_type = item.get("type", "location")
            address = item.get("address", {})
            for key in ("city", "town", "village", "hamlet"):
                if key in address:
                    location_type = key
                    break

            results.append({
                "id": item_id,
                "display_name": item.get("display_name", ""),
                "lat": float(item["lat"]),
                "lon": float(item["lon"]),
                "type": location_type,
                "importance": item.get("importance", 0.5),
            })

        results.sort(key=lambda x: x["importance"], reverse=True)
        return Response(GeocodeSearchResultSerializer(results, many=True).data)

    except Exception as e:
        logger.error(f"Geocode search error: {e}")
        return Response([])


@api_view(["GET"])
@permission_classes([AllowAny])
def poi_photos(request):
    """
    Get photos for a POI from multiple sources with intelligent prioritisation:
    1. Main image from Wikipedia
    2. Geosearch from Wikimedia Commons
    3. Pic4Carto as fallback
    """
    try:
        name = request.GET.get("name", "")
        lat = request.GET.get("lat")
        lon = request.GET.get("lon")

        if not lat or not lon:
            logger.warning(f"Missing coordinates for '{name}'")
            return Response({"photos": [], "wikipedia_description": ""})

        cache_key = f"poi_photos_{float(lat):.5f}_{float(lon):.5f}"
        cached_data = cache.get(cache_key)
        if cached_data:
            logger.info(f"Cache hit for {cache_key}")
            return Response(cached_data)

        logger.info(f"Searching photos for '{name}' at {lat}, {lon}")

        wikimedia_url = os.environ.get("WIKIMEDIA_API_URL")
        wikipedia_url = os.environ.get("WIKIPEDIA_API_URL")
        pic4carto_base = os.environ.get("PIC4CARTO_API_URL")
        headers = {"User-Agent": os.environ.get("API_USER_AGENT", "ApexGPS/1.0 (https://apexgps.com)")}

        photos: list = []
        wikipedia_description = ""

        if wikipedia_url and name:
            wiki_image = _fetch_wikipedia_image(name, wikipedia_url, headers)
            if wiki_image:
                photos.append(wiki_image)
                logger.info(f"Found Wikipedia image for '{name}'")

        if len(photos) < 3 and wikimedia_url:
            existing_urls = {p["url"] for p in photos}
            for p in _fetch_wikimedia_geosearch(lat, lon, wikimedia_url, headers):
                if p["url"] not in existing_urls:
                    photos.append(p)
                    existing_urls.add(p["url"])
            logger.info(f"Wikimedia photos total after merge: {len(photos)}")

        if len(photos) < 3 and pic4carto_base:
            existing_urls = {p["url"] for p in photos}
            for p in _fetch_pic4carto(lat, lon, f"{pic4carto_base}/search/around", headers):
                if p["url"] not in existing_urls:
                    photos.append(p)
                    existing_urls.add(p["url"])
            logger.info(f"Pic4Carto photos total after merge: {len(photos)}")

        if wikipedia_url and name:
            wikipedia_description = _fetch_wikipedia_description(lat, lon, name, wikipedia_url, headers)

        photos = photos[:5]
        logger.info(f"Total photos for '{name}': {len(photos)}")

        response_data = {"photos": photos, "wikipedia_description": wikipedia_description}
        cache.set(cache_key, response_data, timeout=86400)

        return Response(POIPhotoResponseSerializer(response_data).data)

    except Exception as e:
        logger.error(f"Error fetching photos: {e}", exc_info=True)
        return Response({"photos": [], "wikipedia_description": ""})
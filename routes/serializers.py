from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from rest_framework import serializers
from rest_framework_gis.serializers import GeoFeatureModelSerializer

from .models import Route, Stop

User = get_user_model()

__all__ = [
    "StopSerializer",
    "RouteSerializer",
    "RouteCreateSerializer",
    "RouteUpdateSerializer",
    "RouteGeoSerializer",
    "RouteCoordinatesInputSerializer",
    "RouteLocationNamesInputSerializer",
    "RouteCalculationResultSerializer",
    "RouteCalculationResponseSerializer",
]


class StopSerializer(serializers.ModelSerializer):
    """
    Serializer for the Stop model.
    Handles user-added stops along a route.
    """

    class Meta:
        """Meta class for Stop serializer."""

        model = Stop
        fields = [
            "id",
            "route",
            "order",
            "location",
            "name",
            "added_at",
        ]
        read_only_fields = ["id", "added_at"]

    def to_representation(self, instance):
        """Convert location Point to lat/lon dict in response."""
        data = super().to_representation(instance)
        if instance.location:
            data["location"] = {"lat": instance.location.y, "lon": instance.location.x}
        return data

    def to_internal_value(self, data):
        """Convert lat/lon dict to Point object when creating/updating."""
        # Handle location as dict
        location_data = data.get("location")
        if location_data and isinstance(location_data, dict):
            lat = location_data.get("lat")
            lon = location_data.get("lon")
            if lat is not None and lon is not None:
                data["location"] = Point(float(lon), float(lat))
        # Handle direct lat/lon fields
        elif "lat" in data and "lon" in data:
            lat = data.get("lat")
            lon = data.get("lon")
            if lat is not None and lon is not None:
                data["location"] = Point(float(lon), float(lat))
                # Remove lat/lon from data to avoid validation errors
                data.pop("lat")
                data.pop("lon")

        return super().to_internal_value(data)


class RouteCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for creating new routes.
    Only name, visibility, and preference are required initially.
    Coordinates can be added later.
    """

    owner_username = serializers.ReadOnlyField(source="owner.username")

    class Meta:
        """Meta class for Route creation serializer."""

        model = Route
        fields = [
            "id",
            "name",
            "owner_username",
            "visibility",
            "preference",
            "start_location",
            "end_location",
            "created_at",
        ]
        read_only_fields = ["id", "owner_username", "created_at"]

    def to_representation(self, instance):
        """Convert PointFields to lat/lon dicts in response."""
        data = super().to_representation(instance)

        # Convert start_location
        if instance.start_location:
            data["start_location"] = {
                "lat": instance.start_location.y,
                "lon": instance.start_location.x,
            }

        # Convert end_location
        if instance.end_location:
            data["end_location"] = {
                "lat": instance.end_location.y,
                "lon": instance.end_location.x,
            }

        return data

    def to_internal_value(self, data):
        """Convert lat/lon dicts to Point objects when creating/updating."""
        # Handle start_location
        start_loc = data.get("start_location")
        if start_loc and isinstance(start_loc, dict):
            lat = start_loc.get("lat")
            lon = start_loc.get("lon")
            if lat is not None and lon is not None:
                data["start_location"] = Point(float(lon), float(lat))
        elif "start_lat" in data and "start_lon" in data:
            # Alternative format
            lat = data.get("start_lat")
            lon = data.get("start_lon")
            if lat is not None and lon is not None:
                data["start_location"] = Point(float(lon), float(lat))
                data.pop("start_lat")
                data.pop("start_lon")

        # Handle end_location
        end_loc = data.get("end_location")
        if end_loc and isinstance(end_loc, dict):
            lat = end_loc.get("lat")
            lon = end_loc.get("lon")
            if lat is not None and lon is not None:
                data["end_location"] = Point(float(lon), float(lat))
        elif "end_lat" in data and "end_lon" in data:
            # Alternative format
            lat = data.get("end_lat")
            lon = data.get("end_lon")
            if lat is not None and lon is not None:
                data["end_location"] = Point(float(lon), float(lat))
                data.pop("end_lat")
                data.pop("end_lon")

        return super().to_internal_value(data)

    def create(self, validated_data):
        """Create a new Route instance, automatically setting the owner."""
        validated_data["owner"] = self.context["request"].user
        return super().create(validated_data)


class RouteUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer for updating existing routes.
    Allows updating coordinates and other fields after creation.
    """

    owner_username = serializers.ReadOnlyField(source="owner.username")
    stops = StopSerializer(many=True, read_only=True)
    stop_count = serializers.IntegerField(source="get_stops_count", read_only=True)

    class Meta:
        """Meta class for Route update serializer."""

        model = Route
        fields = [
            "id",
            "name",
            "owner",
            "owner_username",
            "visibility",
            "start_location",
            "end_location",
            "preference",
            "polyline",
            "distance_km",
            "estimated_time_min",
            "total_scenic_score",
            "created_at",
            "updated_at",
            "stops",
            "stop_count",
        ]
        read_only_fields = [
            "id",
            "owner",
            "owner_username",
            "created_at",
            "updated_at",
            "stops",
            "stop_count",
            "polyline",
            "distance_km",
            "estimated_time_min",
            "total_scenic_score",
        ]

    def to_representation(self, instance):
        """Convert PointFields to lat/lon dicts in response."""
        data = super().to_representation(instance)

        # Convert start_location
        if instance.start_location:
            data["start_location"] = {
                "lat": instance.start_location.y,
                "lon": instance.start_location.x,
            }

        # Convert end_location
        if instance.end_location:
            data["end_location"] = {
                "lat": instance.end_location.y,
                "lon": instance.end_location.x,
            }

        return data

    def to_internal_value(self, data):
        """Convert lat/lon dicts to Point objects when creating/updating."""
        # Handle start_location
        start_loc = data.get("start_location")
        if start_loc and isinstance(start_loc, dict):
            lat = start_loc.get("lat")
            lon = start_loc.get("lon")
            if lat is not None and lon is not None:
                data["start_location"] = Point(float(lon), float(lat))
        elif "start_lat" in data and "start_lon" in data:
            # Alternative format
            lat = data.get("start_lat")
            lon = data.get("start_lon")
            if lat is not None and lon is not None:
                data["start_location"] = Point(float(lon), float(lat))
                data.pop("start_lat")
                data.pop("start_lon")

        # Handle end_location
        end_loc = data.get("end_location")
        if end_loc and isinstance(end_loc, dict):
            lat = end_loc.get("lat")
            lon = end_loc.get("lon")
            if lat is not None and lon is not None:
                data["end_location"] = Point(float(lon), float(lat))
        elif "end_lat" in data and "end_lon" in data:
            # Alternative format
            lat = data.get("end_lat")
            lon = data.get("end_lon")
            if lat is not None and lon is not None:
                data["end_location"] = Point(float(lon), float(lat))
                data.pop("end_lat")
                data.pop("end_lon")

        return super().to_internal_value(data)


class RouteSerializer(serializers.ModelSerializer):
    """
    Legacy serializer - now acts as read-only for backward compatibility.
    For new code, use RouteCreateSerializer or RouteUpdateSerializer.
    """

    owner_username = serializers.ReadOnlyField(source="owner.username")
    stops = StopSerializer(many=True, read_only=True)
    stop_count = serializers.IntegerField(source="get_stops_count", read_only=True)

    class Meta:
        """Meta class for Route serializer."""

        model = Route
        fields = [
            "id",
            "name",
            "owner",
            "owner_username",
            "visibility",
            "start_location",
            "end_location",
            "preference",
            "polyline",
            "distance_km",
            "estimated_time_min",
            "total_scenic_score",
            "created_at",
            "updated_at",
            "stops",
            "stop_count",
        ]
        read_only_fields = [
            "id",
            "owner",
            "owner_username",
            "created_at",
            "updated_at",
            "stops",
            "stop_count",
            "polyline",
            "distance_km",
            "estimated_time_min",
            "total_scenic_score",
        ]

    def to_representation(self, instance):
        """Convert PointFields to lat/lon dicts in response."""
        data = super().to_representation(instance)

        # Convert start_location
        if instance.start_location:
            data["start_location"] = {
                "lat": instance.start_location.y,
                "lon": instance.start_location.x,
            }

        # Convert end_location
        if instance.end_location:
            data["end_location"] = {
                "lat": instance.end_location.y,
                "lon": instance.end_location.x,
            }

        return data


class RouteGeoSerializer(GeoFeatureModelSerializer):
    """
    GeoJSON serializer for Route model.
    Provides GeoJSON format for spatial visualization of routes.
    """

    class Meta:
        """Meta class for Route GeoJSON model."""

        model = Route
        geo_field = "start_location"
        fields = ["id", "name", "owner", "visibility", "distance_km"]


class RouteCoordinatesInputSerializer(serializers.Serializer):
    """Serializer for direct coordinate input."""

    start_lat = serializers.FloatField(
        required=True,
        min_value=-90.0,
        max_value=90.0,
        help_text="Start latitude",
    )
    start_lon = serializers.FloatField(
        required=True,
        min_value=-180.0,
        max_value=180.0,
        help_text="Start longitude",
    )
    end_lat = serializers.FloatField(
        required=True,
        min_value=-90.0,
        max_value=90.0,
        help_text="End latitude",
    )
    end_lon = serializers.FloatField(
        required=True,
        min_value=-180.0,
        max_value=180.0,
        help_text="End longitude",
    )

    # Optional fields for display
    start_location_name = serializers.CharField(
        required=False,
        max_length=255,
        help_text="Start location name (for display only)",
    )
    end_location_name = serializers.CharField(
        required=False,
        max_length=255,
        help_text="End location name (for display only)",
    )

    vertex_threshold = serializers.FloatField(
        required=False,
        min_value=0.0001,
        max_value=0.1,
        default=0.01,
        help_text="Vertex snapping threshold in degrees (~1km)",
    )

    def validate(self, data):
        """Validate coordinates and ensure Italy bounds."""
        data = super().validate(data)
        return RouteCoordinatesInputValidator.validate(data)


class RouteLocationNamesInputSerializer(serializers.Serializer):
    """Serializer for location name input."""

    start_location_name = serializers.CharField(
        required=True,
        max_length=255,
        help_text="Start location name (e.g., 'Roma, Italia')",
    )
    end_location_name = serializers.CharField(
        required=True,
        max_length=255,
        help_text="End location name (e.g., 'Milano, Italia')",
    )

    vertex_threshold = serializers.FloatField(
        required=False,
        min_value=0.0001,
        max_value=0.1,
        default=0.01,
        help_text="Vertex snapping threshold in degrees (~1km)",
    )

    def validate(self, data):
        """Validate and geocode location names."""
        data = super().validate(data)
        return RouteLocationNamesValidator.validate_and_geocode(data)


class RouteCalculationResultSerializer(serializers.Serializer):
    """
    Serializer for route calculation results.
    Used only for API response, not for saving to database.
    """

    # Basic route info
    preference = serializers.CharField(help_text="Routing preference used")
    total_distance_km = serializers.FloatField(help_text="Total distance in km")
    total_time_minutes = serializers.FloatField(help_text="Total time in minutes")

    # For map display
    polyline = serializers.CharField(
        required=False, help_text="Encoded polyline for map"
    )

    # Time constraint info
    time_constraint_respected = serializers.BooleanField(
        required=False, help_text="Whether time constraint was respected"
    )
    time_exceeded_minutes = serializers.FloatField(
        required=False, help_text="Minutes exceeded beyond constraint"
    )

    # Scenic metrics
    total_scenic_score = serializers.FloatField(
        required=False, help_text="Total scenic score along route"
    )
    avg_scenic_rating = serializers.FloatField(
        required=False, help_text="Average scenic rating"
    )

    # For saving to database
    can_save = serializers.BooleanField(
        help_text="Whether this route can be saved to database"
    )

    def to_representation(self, instance):
        """Format the response with human-readable fields."""
        data = super().to_representation(instance)
        data = RouteResponseFormatter.format_route_response(data)
        return data


class RouteCalculationResponseSerializer(serializers.Serializer):
    """Complete response for route calculation."""

    # Fastest route (benchmark - hidden from user but needed for constraint)
    fastest_route = RouteCalculationResultSerializer(
        required=False, help_text="Fastest route (benchmark, hidden from user)"
    )

    # Calculated routes
    calculated_routes = serializers.DictField(
        child=RouteCalculationResultSerializer(),
        help_text="Calculated scenic routes by preference",
    )

    # Best route by preference
    best_route = RouteCalculationResultSerializer(
        help_text="Best scenic route for the requested preference"
    )

    # Validation info
    validation = serializers.DictField(help_text="Validation results and warnings")

    # Processing info
    processing_time_ms = serializers.FloatField(
        help_text="Processing time in milliseconds"
    )


# Helper classes for validation
class RouteCoordinatesInputValidator:
    """Utility class for validating coordinate input."""

    @staticmethod
    def validate(data):
        """Validate coordinates and ensure Italy bounds."""
        ITALY_BOUNDS = {
            "min_lat": 35.0,
            "max_lat": 47.0,
            "min_lon": 6.0,
            "max_lon": 19.0,
        }

        for name, lat, lon in [
            ("start", data["start_lat"], data["start_lon"]),
            ("end", data["end_lat"], data["end_lon"]),
        ]:
            if not (ITALY_BOUNDS["min_lat"] <= lat <= ITALY_BOUNDS["max_lat"]):
                raise serializers.ValidationError(
                    {
                        f"{name}_coordinates": f"Latitude {lat} "
                        f"is outside Italy bounds (35° to 47°). "
                        f"Please choose a location within Italy."
                    }
                )
            if not (ITALY_BOUNDS["min_lon"] <= lon <= ITALY_BOUNDS["max_lon"]):
                raise serializers.ValidationError(
                    {
                        f"{name}_coordinates": f"Longitude {lon} "
                        f"is outside Italy bounds (6° to 19°). "
                        f"Please choose a location within Italy."
                    }
                )

        # Generate location names if not provided
        if not data.get("start_location_name"):
            data[
                "start_location_name"
            ] = f"{data['start_lat']:.4f}, {data['start_lon']:.4f}"

        if not data.get("end_location_name"):
            data["end_location_name"] = f"{data['end_lat']:.4f}, {data['end_lon']:.4f}"

        return data


class RouteLocationNamesValidator:
    """Utility class for validating and geocoding location names."""

    @staticmethod
    def validate_and_geocode(data):
        """Validate input and geocode location names."""
        try:
            from routes.services.geocoding import GeocodingService
        except ImportError as e:
            raise serializers.ValidationError(
                {"error": "Geocoding service not available"}
            ) from e

        # Geocode start location
        start_location_name = data["start_location_name"]
        start_point = GeocodingService.geocode_location(start_location_name)

        if not start_point:
            raise serializers.ValidationError(
                {
                    "start_location_name": f"Cannot find coordinates for"
                    f" '{start_location_name}'"
                }
            )

        data["start_lat"] = start_point.y
        data["start_lon"] = start_point.x
        data["geocoded_start"] = True

        # Geocode end location
        end_location_name = data["end_location_name"]
        end_point = GeocodingService.geocode_location(end_location_name)

        if not end_point:
            raise serializers.ValidationError(
                {
                    "end_location_name": f"Cannot find coordinates for"
                    f" '{end_location_name}'"
                }
            )

        data["end_lat"] = end_point.y
        data["end_lon"] = end_point.x
        data["geocoded_end"] = True

        return RouteCoordinatesInputValidator.validate(data)


class RouteResponseFormatter:
    """Utility class for formatting route responses."""

    @staticmethod
    def format_route_response(data):
        """Add human-readable fields to route response."""
        if "total_time_minutes" in data:
            hours = int(data["total_time_minutes"] // 60)
            minutes = int(data["total_time_minutes"] % 60)
            data["total_time_formatted"] = f"{hours}h {minutes}min"

        if "total_distance_km" in data:
            data["total_distance_formatted"] = f"{data['total_distance_km']:.1f} km"

        return data

from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from rest_framework import serializers
from rest_framework_gis.serializers import GeoFeatureModelSerializer

from .models import Route, Stop
from .services.routing.route_validator import RouteValidator

User = get_user_model()

__all__ = [
    "StopSerializer",
    "RouteSerializer",
    "RouteValidator",
    "RouteGeoSerializer",
    "RouteCalculationInputSerializer",
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


class RouteSerializer(serializers.ModelSerializer):
    """
    Serializer for the Route model.
    Converts Route model instances to JSON format and vice versa,
    handling data validation and relationships with other models.
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
        read_only_fields = ["owner", "created_at", "updated_at", "stops", "stop_count"]

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
            data["end_location"] = Point(float(lon), float(lat))
            data.pop("end_lat")
            data.pop("end_lon")

        return super().to_internal_value(data)

    def create(self, validated_data):
        """Create a new Route instance, automatically setting the owner."""
        validated_data["owner"] = self.context["request"].user
        return super().create(validated_data)


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


class RouteCalculationInputSerializer(serializers.Serializer):
    """
    Serializer for route calculation input.
    Separate from RouteSerializer as this is for calculation, not CRUD.
    """

    # Start coordinates
    start_lat = serializers.FloatField(
        required=True,
        min_value=-90,
        max_value=90,
        help_text="Start latitude (-90 to 90)",
    )
    start_lon = serializers.FloatField(
        required=True,
        min_value=-180,
        max_value=180,
        help_text="Start longitude (-180 to 180)",
    )

    # End coordinates
    end_lat = serializers.FloatField(
        required=True, min_value=-90, max_value=90, help_text="End latitude (-90 to 90)"
    )
    end_lon = serializers.FloatField(
        required=True,
        min_value=-180,
        max_value=180,
        help_text="End longitude (-180 to 180)",
    )

    # Routing preference (for future use with scenic routes)
    preference = serializers.ChoiceField(
        required=False,
        choices=[
            ("fast", "Veloce"),
            ("balanced", "Equilibrata"),
            ("most_winding", "Sinuosa Massima"),
        ],
        default="balanced",
        help_text="Routing preference (for scenic routes)",
    )

    # Time constraint (40 minutes = 0.67 hours, we'll use percentage)
    max_time_increase_pct = serializers.FloatField(
        required=False,
        min_value=0.1,
        max_value=1.0,
        default=0.5,
        help_text="Maximum time increase as percentage"
        " (0.5 = 50% = 40min on 80min base)",
    )

    # Advanced parameters
    vertex_threshold = serializers.FloatField(
        required=False,
        min_value=0.0001,
        max_value=0.1,
        default=0.01,
        help_text="Vertex snapping threshold in degrees (~1km)",
    )

    include_fastest = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Include fastest route in response (for benchmarking)",
    )

    def validate(self, data):
        """Custom validation for coordinate pairs."""
        # Validate coordinates are within Italy bounds
        start_lat, start_lon = data.get("start_lat"), data.get("start_lon")
        end_lat, end_lon = data.get("end_lat"), data.get("end_lon")

        # Italy bounds check
        ITALY_BOUNDS = {
            "min_lat": 35.0,
            "max_lat": 47.0,
            "min_lon": 6.0,
            "max_lon": 19.0,
        }

        for name, lat, lon in [
            ("start", start_lat, start_lon),
            ("end", end_lat, end_lon),
        ]:
            if not (ITALY_BOUNDS["min_lat"] <= lat <= ITALY_BOUNDS["max_lat"]):
                raise serializers.ValidationError(
                    {name: f"Latitude {lat} is outside Italy bounds (35° to 47°)"}
                )
            if not (ITALY_BOUNDS["min_lon"] <= lon <= ITALY_BOUNDS["max_lon"]):
                raise serializers.ValidationError(
                    {name: f"Longitude {lon} is outside Italy bounds (6° to 19°)"}
                )

        return data


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
        """Format the response."""
        data = super().to_representation(instance)

        # Add human-readable fields
        if "total_time_minutes" in data:
            hours = int(data["total_time_minutes"] // 60)
            minutes = int(data["total_time_minutes"] % 60)
            data["total_time_formatted"] = f"{hours}h {minutes}min"

        if "total_distance_km" in data:
            data["total_distance_formatted"] = f"{data['total_distance_km']:.1f} km"

        return data


class RouteCalculationResponseSerializer(serializers.Serializer):
    """
    Complete response for route calculation.
    Used for scenic routes (to be implemented in PR #3).
    """

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

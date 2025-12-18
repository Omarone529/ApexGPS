from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from rest_framework import serializers
from rest_framework_gis.serializers import GeoFeatureModelSerializer

from .models import Route, Stop

User = get_user_model()


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

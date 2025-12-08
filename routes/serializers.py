from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework_gis.serializers import GeoFeatureModelSerializer

from .models import Route

User = get_user_model()


class RouteSerializer(serializers.ModelSerializer):
    """
    Serializer for the Route model.
    Converts Route model instances to JSON format and vice versa,
    handling data validation and relationships with other models.
    """

    owner_username = serializers.ReadOnlyField(source="owner.username")

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
        ]
        read_only_fields = ["owner", "created_at", "updated_at"]

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

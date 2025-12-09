from django.contrib.gis.geos import Point
from rest_framework import serializers

from .models import PointOfInterest, ScenicArea


class PointOfInterestSerializer(serializers.ModelSerializer):
    """
    Serializer for PointOfInterest model.
    Handles latitude/longitude conversion for the PointField.
    Accepts either:
    - location field (WKT or GeoJSON)
    - OR latitude and longitude fields (converted to Point).
    """

    latitude = serializers.FloatField(write_only=True, required=False)
    longitude = serializers.FloatField(write_only=True, required=False)

    class Meta:
        """Meta class for PointOfInterest model."""

        model = PointOfInterest
        fields = [
            "id",
            "name",
            "category",
            "location",
            "description",
            "latitude",
            "longitude",
        ]
        read_only_fields = ["id"]
        extra_kwargs = {"location": {"required": False, "write_only": True}}

    def validate(self, data):
        """Custom validation to ensure location or lat/lon are provided."""
        # Check if location is provided directly
        if "location" in data:
            return data
        # Check if latitude and longitude are both provided
        latitude = data.get("latitude")
        longitude = data.get("longitude")

        if latitude is not None and longitude is not None:
            return data

        # Check if only one of lat/lon is provided
        if (latitude is not None and longitude is None) or (
            latitude is None and longitude is not None
        ):
            raise serializers.ValidationError(
                "Both latitude and longitude must be provided together."
            )
        # Neither location nor lat/lon provided
        raise serializers.ValidationError(
            "Either 'location' or both 'latitude' and 'longitude' are required."
        )

    def create(self, validated_data):
        """Extract lat/lon if provided, otherwise use location from model."""
        latitude = validated_data.pop("latitude", None)
        longitude = validated_data.pop("longitude", None)

        if latitude is not None and longitude is not None:
            # Create Point from lat/lon
            validated_data["location"] = Point(longitude, latitude, srid=4326)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        """Update a PointOfInterest instance with latitude/longitude conversion."""
        latitude = validated_data.pop("latitude", None)
        longitude = validated_data.pop("longitude", None)

        if latitude is not None and longitude is not None:
            validated_data["location"] = Point(longitude, latitude, srid=4326)

        return super().update(instance, validated_data)

    def to_representation(self, instance):
        """Convert PointField to lat/lon in response."""
        representation = super().to_representation(instance)
        if instance.location:
            representation["latitude"] = instance.location.y
            representation["longitude"] = instance.location.x
        return representation


class ScenicAreaSerializer(serializers.ModelSerializer):
    """Serializer for ScenicArea model."""

    class Meta:
        """Meta class for ScenicArea model."""

        model = ScenicArea
        fields = ["id", "name", "area_type", "bonus_value", "area"]
        read_only_fields = ["id"]

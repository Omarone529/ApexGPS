from django.contrib.gis.geos import Point
from rest_framework import serializers

from .models import PointOfInterest, ScenicArea


class PointOfInterestSerializer(serializers.ModelSerializer):
    """
    Serializer for PointOfInterest model.
    Handles latitude/longitude conversion for the PointField.
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

    def create(self, validated_data):
        """Extract lat/lon if provided, otherwise use location from model."""
        latitude = validated_data.pop("latitude", None)
        longitude = validated_data.pop("longitude", None)

        if latitude is not None and longitude is not None:
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

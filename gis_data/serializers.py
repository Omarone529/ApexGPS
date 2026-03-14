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
            "region",
            "importance_score",
            "is_verified",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        extra_kwargs = {"location": {"required": False, "write_only": True}}

    def validate(self, data):
        """Custom validation to ensure location or lat/lon are provided."""
        if self.partial:
            return data
        if "location" in data:
            return data
        latitude = data.get("latitude")
        longitude = data.get("longitude")

        if latitude is not None and longitude is not None:
            return data
        if (latitude is not None and longitude is None) or (
                latitude is None and longitude is not None
        ):
            raise serializers.ValidationError(
                "Both latitude and longitude must be provided together."
            )
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
        fields = ["id", "name", "area_type", "bonus_value", "area", "description"]
        read_only_fields = ["id"]

class POIPhotoSerializer(serializers.Serializer):
    """Serializer for individual POI photos."""

    id = serializers.CharField()
    url = serializers.URLField()
    thumbnail = serializers.URLField()
    width = serializers.IntegerField(required=False, allow_null=True)
    height = serializers.IntegerField(required=False, allow_null=True)
    source = serializers.CharField(default='Google Places')


class PlaceDetailsSerializer(serializers.Serializer):
    """Serializer for Google Places details."""

    place_id = serializers.CharField()
    name = serializers.CharField(required=False, allow_blank=True)
    formatted_address = serializers.CharField(required=False, allow_blank=True)
    rating = serializers.FloatField(required=False, allow_null=True)
    user_ratings_total = serializers.IntegerField(required=False, allow_null=True)


class POIPhotosResponseSerializer(serializers.Serializer):
    """Complete response serializer for POI photos endpoint."""

    photos = POIPhotoSerializer(many=True, default=[])
    place_details = PlaceDetailsSerializer(required=False, allow_null=True)
    source = serializers.CharField(default='google_places')
    configured = serializers.BooleanField(default=True)
    error = serializers.CharField(required=False, allow_blank=True)

    wikipedia_description = serializers.CharField(
        required=False,
        allow_blank=True,
        default='',
        help_text='Descrizione dal POI (per compatibilità)'
    )
from rest_framework import serializers

from .models import ElevationQuery


class ElevationQuerySerializer(serializers.ModelSerializer):
    """Serializer for Elevation query."""

    class Meta:
        """Meta class for ElevationQuerySerializer."""

        model = ElevationQuery
        fields = [
            "id",
            "name",
            "latitude",
            "longitude",
            "elevation",
            "success",
            "queried_at",
        ]
        read_only_fields = ["elevation", "success", "queried_at"]

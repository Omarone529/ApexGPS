import base64
import uuid

from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.files.base import ContentFile
from rest_framework import serializers
from rest_framework_gis.serializers import GeoFeatureModelSerializer
import hashlib
import json

from .models import Route, Stop

User = get_user_model()

__all__ = [
    "StopSerializer",
    "RouteSerializer",
    "RouteCreateSerializer",
    "RouteUpdateSerializer",
    "RouteGeoSerializer",
    "RouteCalculationInputSerializer",
    "RouteCalculationResultSerializer",
    "RouteCalculationResponseSerializer",
    "RouteSaveFromCalculationSerializer",
    "GeocodeSearchResultSerializer",
    "POIPhotoResponseSerializer"
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
            "screenshot",
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
            "screenshot",
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
            "screenshot",
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
    hidden_until = serializers.SerializerMethodField()

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
            "screenshot",
            "created_at",
            "updated_at",
            "stops",
            "stop_count",
            "hidden_until",
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
            "screenshot",
        ]

    def get_hidden_until(self, obj):
        request = self.context.get('request')
        if request and request.user.is_staff:
            return obj.hiddenUntil
        return None

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


class RouteCalculationInputSerializer(serializers.Serializer):
    """
    Serializer for route calculation input.
    Accepts location names which are automatically geocoded to coordinates.
    """

    start_location_name = serializers.CharField(
        required=True,
        max_length=255,
        help_text="Start location (e.g., 'Roma' or 'Milano')",
    )
    end_location_name = serializers.CharField(
        required=True,
        max_length=255,
        help_text="End location (e.g., 'Firenze' or 'Napoli')",
    )
    waypoints = serializers.ListField(
        child=serializers.CharField(max_length=255),
        required=False,
        default=[],
        help_text="Intermediate waypoints (optional)",
    )
    vertex_threshold = serializers.FloatField(
        required=False,
        min_value=0.0001,
        max_value=0.1,
        default=0.01,
        help_text="Vertex snapping threshold in degrees (~1km)",
    )

    def validate(self, data):
        """Validate input and geocode location names."""
        try:
            from routes.services.geocoding import GeocodingService
        except ImportError as e:
            raise serializers.ValidationError(
                {"error": "Geocoding service not available"}
            ) from e

        def clean_string(value):
            if not value:
                return value
            value = str(value).strip()
            if (value.startswith('"') and value.endswith('"')) or (
                    value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            return value.strip()

        start_location_name = clean_string(data["start_location_name"])
        end_location_name = clean_string(data["end_location_name"])

        def normalize_location(city_name):
            city = city_name.strip()

            if not city:
                return city
            city_lower = city.lower()
            country_keywords = [
                "italia",
                "italy",
                "it",
                "france",
                "spain",
                "germany",
                "de",
                "fr",
                "es",
            ]

            if any(keyword in city_lower for keyword in country_keywords):
                return city
            return f"{city}, Italia"

        start_location_name_normalized = normalize_location(start_location_name)
        end_location_name_normalized = normalize_location(end_location_name)

        def geocode_with_fallback(location_name, original_name, field_name):
            try:
                point = GeocodingService.geocode_location(location_name)

                if not point:
                    point = GeocodingService.geocode_location(original_name)

                    if not point:
                        if ", Italia" in location_name:
                            point = GeocodingService.geocode_location(
                                location_name.replace(", Italia", ", Italy")
                            )
                        elif ", Italy" in location_name:
                            point = GeocodingService.geocode_location(
                                location_name.replace(", Italy", ", Italia")
                            )

                if not point:
                    fallback_variants = [
                        original_name,
                        f"{original_name}, Italy",
                        f"{original_name}, IT",
                    ]

                    for variant in fallback_variants:
                        point = GeocodingService.geocode_location(variant)
                        if point:
                            break

                if not point:
                    raise serializers.ValidationError(
                        {
                            field_name: f"Cannot find coordinates for"
                                        f" '{original_name}'. "
                                        f"Please try a different location name"
                                        f" or be more specific."
                        }
                    )

                return point

            except Exception as e:
                raise serializers.ValidationError(
                    {field_name: f"Geocoding error: {str(e)}"}
                ) from e

        # Geocode start location
        start_point = geocode_with_fallback(
            start_location_name_normalized, start_location_name, "start_location_name"
        )

        data["start_lat"] = start_point.y
        data["start_lon"] = start_point.x
        data["geocoded_start"] = True
        data["start_location_name"] = start_location_name_normalized

        # Geocode end location
        end_point = geocode_with_fallback(
            end_location_name_normalized, end_location_name, "end_location_name"
        )

        data["end_lat"] = end_point.y
        data["end_lon"] = end_point.x
        data["geocoded_end"] = True
        data["end_location_name"] = end_location_name_normalized

        # Geocode waypoints if any
        waypoints = data.get("waypoints", [])
        geocoded_waypoints = []
        for i, wp_name in enumerate(waypoints):
            if wp_name and wp_name.strip():
                wp_name_clean = clean_string(wp_name)
                wp_name_normalized = normalize_location(wp_name_clean)
                wp_point = geocode_with_fallback(
                    wp_name_normalized, wp_name_clean, f"waypoints[{i}]"
                )
                geocoded_waypoints.append({
                    "original_name": wp_name_clean,
                    "name": wp_name_normalized,
                    "lat": wp_point.y,
                    "lon": wp_point.x,
                })

        data["geocoded_waypoints"] = geocoded_waypoints

        # Italy bounds check
        ITALY_BOUNDS = {
            "min_lat": 35.0,
            "max_lat": 47.0,
            "min_lon": 6.0,
            "max_lon": 19.0,
        }

        all_points = [(data["start_lat"], data["start_lon"])] + \
                     [(wp["lat"], wp["lon"]) for wp in geocoded_waypoints] + \
                     [(data["end_lat"], data["end_lon"])]

        for i, (lat, lon) in enumerate(all_points):
            point_type = "start" if i == 0 else "end" if i == len(all_points) - 1 else f"waypoint {i}"
            if not (ITALY_BOUNDS["min_lat"] <= lat <= ITALY_BOUNDS["max_lat"]):
                raise serializers.ValidationError(
                    {
                        point_type: f"Latitude {lat} is outside Italy bounds (35° to 47°). "
                                    f"Please choose a location within Italy."
                    }
                )
            if not (ITALY_BOUNDS["min_lon"] <= lon <= ITALY_BOUNDS["max_lon"]):
                raise serializers.ValidationError(
                    {
                        point_type: f"Longitude {lon} is outside Italy bounds (6° to 19°). "
                                    f"Please choose a location within Italy."
                    }
                )

        return data

    def to_representation(self, instance):
        """Format response to include both names and coordinates."""
        data = super().to_representation(instance)

        # add geocoded coordinates to the rensponse
        if hasattr(self, "validated_data"):
            data["start_coordinates"] = {
                "lat": self.validated_data.get("start_lat"),
                "lon": self.validated_data.get("start_lon"),
            }
            data["end_coordinates"] = {
                "lat": self.validated_data.get("end_lat"),
                "lon": self.validated_data.get("end_lon"),
            }

            # Add waypoints coordinates
            geocoded_waypoints = self.validated_data.get("geocoded_waypoints", [])
            if geocoded_waypoints:
                data["waypoints"] = [
                    {
                        "name": wp["name"],
                        "original_name": wp["original_name"],
                        "coordinates": {"lat": wp["lat"], "lon": wp["lon"]}
                    }
                    for wp in geocoded_waypoints
                ]

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


class RouteSaveFromCalculationSerializer(serializers.Serializer):
    """
    Serializer for saving a previously calculated route.
    Accepts the calculation data and creates a new route in the database.
    """

    name = serializers.CharField(
        max_length=255, required=True, help_text="Nome del percorso"
    )
    visibility = serializers.ChoiceField(
        choices=[
            ("private", "Privato"),
            ("public", "Pubblico"),
            ("link", "Condiviso con link"),
        ],
        required=True,
        help_text="Visibilità del percorso",
    )
    calculation_data = serializers.DictField(
        required=True, help_text="Dati del calcolo ottenuti dall'endpoint di calcolo"
    )
    screenshot = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Screenshot del percorso in base64 (data:image/jpeg;base64,...)"
    )

    @staticmethod
    def _generate_fingerprint(calc_data, user_id):
        """
        Generate a SHA256 fingerprint based on:
        - user_id
        - start coordinates (rounded to 5 decimals)
        - end coordinates (rounded to 5 decimals)
        - preference
        - waypoints in order (each rounded to 5 decimals)
        """
        start = calc_data['start_location']
        end = calc_data['end_location']
        start_coords = (round(start.y, 5), round(start.x, 5))
        end_coords = (round(end.y, 5), round(end.x, 5))

        waypoints = calc_data.get('waypoints', [])
        wp_coords = []
        for wp in waypoints:
            lat = wp.get('lat')
            lon = wp.get('lon')
            if lat is not None and lon is not None:
                wp_coords.append((round(lat, 5), round(lon, 5)))

        data_for_hash = {
            'user_id': user_id,
            'start': start_coords,
            'end': end_coords,
            'preference': calc_data.get('preference', 'balanced'),
            'waypoints': wp_coords,
        }
        json_str = json.dumps(data_for_hash, sort_keys=True)
        return hashlib.sha256(json_str.encode('utf-8')).hexdigest()

    def validate(self, data):
        """Verify user permissions according to specifications."""
        user = self.context["request"].user

        if not user.is_authenticated:
            raise serializers.ValidationError(
                "Devi essere autenticato per salvare un percorso"
            )

        if user.is_visitor:
            raise serializers.ValidationError(
                "I visitatori non possono salvare itinerari. Registrati!"
            )

        if data["visibility"] != "public" and not user.can_create_private_routes():
            raise serializers.ValidationError(
                "Solo utenti iscritti possono creare itinerari privati"
            )

        if data["visibility"] == "public" and not user.can_publish_routes():
            raise serializers.ValidationError(
                "Solo utenti iscritti possono pubblicare itinerari"
            )

        # Data validation
        calc_data = data["calculation_data"]
        required_fields = [
            "start_location",
            "end_location",
            "preference",
            "total_distance_km",
            "total_time_minutes",
        ]
        for field in required_fields:
            if field not in calc_data:
                raise serializers.ValidationError(
                    f"Dati di calcolo incompleti: manca '{field}'"
                )

        # Convert locations from dict to Point
        start_loc = calc_data["start_location"]
        if isinstance(start_loc, dict):
            try:
                lat = float(start_loc.get("lat", start_loc.get("y")))
                lon = float(start_loc.get("lon", start_loc.get("x")))
                calc_data["start_location"] = Point(lon, lat, srid=4326)
            except (TypeError, ValueError, KeyError) as e:
                raise serializers.ValidationError(
                    "Formato start_location non valido."
                    " Usa {'lat': 45.4642, 'lon': 9.1900}"
                ) from e

        end_loc = calc_data["end_location"]
        if isinstance(end_loc, dict):
            try:
                lat = float(end_loc.get("lat", end_loc.get("y")))
                lon = float(end_loc.get("lon", end_loc.get("x")))
                calc_data["end_location"] = Point(lon, lat, srid=4326)
            except (TypeError, ValueError, KeyError) as e:
                raise serializers.ValidationError(
                    "Formato end_location non valido."
                    " Usa {'lat': 41.9028, 'lon': 12.4964}"
                ) from e

        # Generate fingerprint and check for duplicates
        fingerprint = self._generate_fingerprint(calc_data, user.id)

        if Route.objects.filter(owner=user, fingerprint=fingerprint).exists():
            raise serializers.ValidationError(
                "Percorso già salvato in precedenza.",
                code='duplicate_route'
            )

        # Store fingerprint to be used in create()
        data['fingerprint'] = fingerprint

        return data

    def create(self, validated_data):
        """Create a new route from the calculation data."""
        user = self.context["request"].user
        calc_data = validated_data["calculation_data"]
        fingerprint = validated_data["fingerprint"]
        screenshot_b64 = validated_data.get("screenshot", "")

        route = Route.objects.create(
            name=validated_data["name"],
            owner=user,
            visibility=validated_data["visibility"],
            preference=calc_data.get("preference", "balanced"),
            start_location=calc_data["start_location"],
            end_location=calc_data["end_location"],
            polyline=calc_data.get("polyline", ""),
            distance_km=calc_data["total_distance_km"],
            estimated_time_min=calc_data["total_time_minutes"],
            total_scenic_score=calc_data.get("total_scenic_score", 0),
            fingerprint=fingerprint,
        )

        if screenshot_b64:
            try:
                format, imgstr = screenshot_b64.split(';base64,')
                ext = format.split('/')[-1]  # es. 'jpeg'
                data = ContentFile(base64.b64decode(imgstr), name=f"{uuid.uuid4()}.{ext}")
                route.screenshot.save(f"route_{route.id}.{ext}", data, save=True)
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Errore nel salvataggio dello screenshot: {e}")


        # Create stops for waypoints if they exist
        waypoints = calc_data.get("waypoints", [])
        for i, wp in enumerate(waypoints):
            Stop.objects.create(
                route=route,
                order=i + 1,
                location=Point(wp["lon"], wp["lat"], srid=4326),
                name=wp.get("name", f"Tappa {i + 1}"),
            )

        return route


class GeocodeSearchResultSerializer(serializers.Serializer):
    """Serializer for geocode search results."""
    id = serializers.CharField()
    display_name = serializers.CharField()
    lat = serializers.FloatField()
    lon = serializers.FloatField()
    type = serializers.CharField()
    importance = serializers.FloatField(required=False, default=0.5)

class POIPhotoSerializer(serializers.Serializer):
    """Serializer for individual POI photos."""
    id = serializers.CharField()
    url = serializers.URLField()
    thumbnail = serializers.URLField()
    date = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    source = serializers.CharField(default='Wikimedia Commons')


class POIPhotoResponseSerializer(serializers.Serializer):
    """
    Serializer for the complete POI photo response.
    Includes photos array and Wikipedia description.
    """
    photos = POIPhotoSerializer(many=True, required=False, default=[])
    wikipedia_description = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        default='',
        help_text='Breve descrizione del luogo da Wikipedia'
    )


class HiddenUntilSerializer(serializers.Serializer):
    hidden_until = serializers.DateTimeField(
        allow_null=True,
        required=False,
        help_text="Data e ora fino a cui il percorso sarà privato. Null per rimuovere il blocco."
    )
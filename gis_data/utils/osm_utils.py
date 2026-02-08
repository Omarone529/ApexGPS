import logging
import os
import re
import time
import math
from typing import Any

import requests
from django.contrib.gis.geos import LineString, Point
logger = logging.getLogger(__name__)


class OSMConfig:
    """Configuration for regional imports."""

    @classmethod
    def get_endpoints(cls) -> list[str]:
        """Get OSM endpoints from environment variables."""
        endpoints = []
        env_vars = ["OSM_URL", "OSM_URL_KUMI", "OSM_URL_NCHC", "OSM_URL_CH"]

        for var_name in env_vars:
            endpoint = os.environ.get(var_name)
            if endpoint and endpoint.startswith(("http://", "https://")):
                endpoints.append(endpoint)

        if not endpoints:
            endpoints = [
                "https://overpass-api.de/api/interpreter",
                "https://overpass.openstreetmap.fr/api/interpreter",
            ]

        return endpoints

    # Highway types to import
    HIGHWAY_TYPES = [
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "tertiary",
        "unclassified",
        "residential",
        "service",
        "track",
        "path",
        "living_street",
        "pedestrian",
        "cycleway",
    ]

    # Italian bounding box
    ITALY_BBOX = (35.5, 6.6, 47.1, 18.5)

    # Italian regions with bounding boxes - AREA TEST AUMENTATA
    REGION_BBOXES = {
        "abruzzo": (41.5, 13.0, 43.0, 15.0),
        "basilicata": (39.8, 15.2, 41.3, 16.8),
        "calabria": (37.9, 15.5, 40.2, 17.2),
        "campania": (39.9, 13.8, 41.5, 15.9),
        "emilia-romagna": (43.5, 9.5, 45.2, 12.8),
        "friuli-venezia_giulia": (45.5, 12.5, 46.8, 14.0),
        "lazio": (41.2, 11.8, 42.8, 13.8),
        "liguria": (43.5, 7.5, 44.7, 10.0),
        "lombardia": (44.7, 8.5, 46.7, 11.8),
        "marche": (42.5, 12.5, 44.0, 14.0),
        "molise": (41.3, 14.0, 42.2, 15.2),
        "piemonte": (44.0, 6.5, 46.5, 9.5),
        "puglia": (39.8, 16.0, 41.9, 18.5),
        "sardegna": (38.8, 8.1, 41.2, 9.8),
        "sicilia": (36.5, 12.4, 38.3, 15.7),
        "toscana": (42.5, 9.8, 44.5, 12.5),
        "trentino-alto_adige": (45.6, 10.4, 47.1, 12.5),
        "umbria": (42.5, 12.0, 43.5, 13.5),
        "valle_daosta": (45.5, 6.8, 46.0, 7.8),
        "veneto": (44.8, 10.5, 46.8, 13.0),
        "test": (41.70, 12.30, 42.10, 12.80),  # AREA AUMENTATA: ~45km x ~45km
        "italy": (35.5, 6.6, 47.1, 18.5),
    }

    ALL_REGIONS = [
        "piemonte",
        "valle_daosta",
        "lombardia",
        "trentino-alto_adige",
        "veneto",
        "friuli-venezia_giulia",
        "liguria",
        "emilia-romagna",
        "toscana",
        "umbria",
        "marche",
        "lazio",
        "abruzzo",
        "molise",
        "campania",
        "puglia",
        "basilicata",
        "calabria",
        "sicilia",
        "sardegna",
    ]

    # Mappatura highway type -> velocità base (km/h)
    HIGHWAY_SPEEDS = {
        "motorway": 130,
        "trunk": 110,
        "primary": 90,
        "secondary": 70,
        "tertiary": 50,
        "unclassified": 50,
        "residential": 30,
        "service": 20,
        "track": 20,
        "path": 10,
        "living_street": 10,
        "pedestrian": 5,
        "cycleway": 15,
        "default": 50,
    }

    # Mappatura highway type -> scenic rating base (1-10)
    HIGHWAY_SCENIC_RATINGS = {
        "motorway": 2.0,
        "trunk": 3.0,
        "primary": 4.0,
        "secondary": 6.0,
        "tertiary": 7.0,
        "unclassified": 8.0,
        "residential": 1.0,
        "service": 1.0,
        "track": 9.0,
        "path": 8.0,
        "living_street": 1.0,
        "pedestrian": 2.0,
        "cycleway": 7.0,
        "default": 5.0,
    }


class OSMQueryBuilder:
    """Builds OSM queries."""

    @staticmethod
    def build_road_query(bbox: tuple[float, float, float, float]) -> str:
        """Build road query for a region."""
        min_lat, min_lon, max_lat, max_lon = bbox

        highway_types = "|".join(OSMConfig.HIGHWAY_TYPES)

        query = f"""
            [out:json][timeout:300];
            way["highway"~"^{highway_types}$"]
            ({min_lat},{min_lon},{max_lat},{max_lon});
            out geom;
        """
        return query.strip()

    @staticmethod
    def build_simple_test_query(bbox: tuple[float, float, float, float]) -> str:
        """Build simple test query."""
        min_lat, min_lon, max_lat, max_lon = bbox
        query = f"""
            [out:json][timeout:180];
            way["highway"~"primary|secondary|tertiary|residential"]
            ({min_lat},{min_lon},{max_lat},{max_lon});
            out geom;
        """
        return query.strip()


class OSMAPIClient:
    """API client for OSM Overpass queries."""

    def __init__(self):
        """Initialize OSM client."""
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "ApexGPS/1.0",
                "Accept": "application/json",
            }
        )
        self.endpoints = OSMConfig.get_endpoints()

    def execute_query(self, query: str) -> dict[str, Any] | None:
        """Execute query with retry logic."""
        for endpoint in self.endpoints:
            for attempt in range(3):
                try:
                    if attempt > 0:
                        time.sleep(30 * attempt)

                    response = self.session.post(
                        endpoint, data={"data": query}, timeout=300
                    )

                    if response.status_code == 200:
                        return response.json()
                    elif response.status_code in [429, 504]:
                        continue

                except requests.exceptions.RequestException:
                    if attempt < 2:
                        continue

        return None


class RoadDataProcessor:
    """Processes road data from OSM responses."""

    @staticmethod
    def calculate_geographic_length(coords: list[tuple[float, float]]) -> float:
        """Calculate accurate geographic length in meters using haversine formula."""
        if len(coords) < 2:
            return 0.0

        total_distance = 0.0

        for i in range(1, len(coords)):
            lon1, lat1 = coords[i - 1]
            lon2, lat2 = coords[i]

            # Convert to radians
            lat1_rad = math.radians(lat1)
            lat2_rad = math.radians(lat2)
            lon1_rad = math.radians(lon1)
            lon2_rad = math.radians(lon2)

            # Haversine formula
            dlat = lat2_rad - lat1_rad
            dlon = lon2_rad - lon1_rad
            a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
            distance = 6371000 * c  # Earth radius in meters

            total_distance += distance

        return total_distance

    @staticmethod
    def calculate_curvature(coords: list[tuple[float, float]]) -> float:
        """Calculate road curvature (0-2 scale)."""
        if len(coords) < 3:
            return 1.0  # Straight road

        total_angle_change = 0.0

        for i in range(1, len(coords) - 1):
            # Calculate vectors
            v1_lon = coords[i][0] - coords[i - 1][0]
            v1_lat = coords[i][1] - coords[i - 1][1]

            v2_lon = coords[i + 1][0] - coords[i][0]
            v2_lat = coords[i + 1][1] - coords[i][1]

            # Calculate dot product
            dot = v1_lon * v2_lon + v1_lat * v2_lat

            # Calculate magnitudes
            mag1 = math.sqrt(v1_lon ** 2 + v1_lat ** 2)
            mag2 = math.sqrt(v2_lon ** 2 + v2_lat ** 2)

            if mag1 == 0 or mag2 == 0:
                continue

            # Calculate angle
            cos_angle = dot / (mag1 * mag2)
            cos_angle = max(-1.0, min(1.0, cos_angle))
            angle = math.acos(cos_angle)

            total_angle_change += abs(angle)

        # Normalize curvature (0 = straight, 2 = very curvy)
        avg_angle_change = total_angle_change / (len(coords) - 2)
        curvature = min(2.0, avg_angle_change * 10)  # Scale factor

        return round(curvature, 3)

    @staticmethod
    def parse_tags(tags: dict) -> dict:
        """Parse road tags."""
        highway_type = tags.get("highway", "unclassified")

        # Get base speed and scenic rating
        base_speed = OSMConfig.HIGHWAY_SPEEDS.get(highway_type, OSMConfig.HIGHWAY_SPEEDS["default"])
        base_scenic = OSMConfig.HIGHWAY_SCENIC_RATINGS.get(highway_type, OSMConfig.HIGHWAY_SCENIC_RATINGS["default"])

        parsed = {
            "maxspeed": RoadDataProcessor._parse_maxspeed(tags.get("maxspeed"), base_speed),
            "oneway": tags.get("oneway") == "yes",
            "surface": tags.get("surface", ""),
            "lanes": RoadDataProcessor._parse_lanes(tags.get("lanes")),
            "name": tags.get("name", ""),
            "highway": highway_type,
            "base_speed_kmh": base_speed,
            "base_scenic_rating": base_scenic,
        }
        return parsed

    @staticmethod
    def _parse_maxspeed(maxspeed_str: str | None, default_speed: int) -> int:
        """Parse maxspeed."""
        if not maxspeed_str:
            return default_speed

        try:
            maxspeed_str = str(maxspeed_str).lower().strip()

            # Remove common units
            for unit in ["km/h", "kmh", "kph"]:
                maxspeed_str = maxspeed_str.replace(unit, "")

            # Extract first number
            numbers = re.findall(r'\d+', maxspeed_str)
            if numbers:
                speed = int(numbers[0])
                # Sanity check
                if 5 <= speed <= 200:
                    return speed

            return default_speed
        except (ValueError, TypeError):
            return default_speed

    @staticmethod
    def _parse_lanes(lanes_str: str | None) -> int | None:
        """Parse lanes."""
        if not lanes_str:
            return None

        try:
            return int(lanes_str)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def extract_coordinates_from_way(way: dict) -> list[tuple[float, float]]:
        """Extract coordinates from way with geometry."""
        coords = []

        if "geometry" in way:
            for point in way["geometry"]:
                if "lon" in point and "lat" in point:
                    coords.append((point["lon"], point["lat"]))

        return coords

    @staticmethod
    def calculate_time_cost(length_m: float, speed_kmh: int) -> float:
        """Calculate time cost in seconds."""
        if length_m <= 0 or speed_kmh <= 0:
            return 0.0

        speed_ms = speed_kmh / 3.6  # Convert km/h to m/s
        return length_m / speed_ms

    @staticmethod
    def create_road_segment(way: dict, region: str = None) -> Any:
        """Create RoadSegment from OSM way with region."""
        if "id" not in way or way.get("type") != "way":
            return None

        tags = way.get("tags", {})
        if "highway" not in tags:
            return None

        coords = RoadDataProcessor.extract_coordinates_from_way(way)
        if len(coords) < 2:
            return None

        try:
            parsed_tags = RoadDataProcessor.parse_tags(tags)
            geometry = LineString(coords, srid=4326)

            # Calculate accurate length in meters
            length_m = RoadDataProcessor.calculate_geographic_length(coords)

            # Calculate curvature
            curvature = RoadDataProcessor.calculate_curvature(coords)

            # Calculate costs
            speed_kmh = parsed_tags["maxspeed"]
            cost_time = RoadDataProcessor.calculate_time_cost(length_m, speed_kmh)
            cost_length = length_m

            # Calculate scenic rating
            base_scenic = parsed_tags["base_scenic_rating"]
            scenic_rating = min(10.0, base_scenic * (1.0 + curvature * 0.3))

            # Calculate scenic cost
            cost_scenic = length_m * (10.0 - scenic_rating) / 10.0

            # Calculate balanced cost
            cost_balanced = (cost_time * 0.6) + (cost_scenic * 0.4)

            from gis_data.models import RoadSegment

            segment = RoadSegment(
                osm_id=way["id"],
                name=parsed_tags["name"],
                highway=parsed_tags["highway"],
                geometry=geometry,
                maxspeed=speed_kmh,
                oneway=parsed_tags["oneway"],
                surface=parsed_tags["surface"],
                lanes=parsed_tags["lanes"],
                length_m=length_m,
                cost_time=cost_time,
                cost_length=cost_length,
                cost_scenic=cost_scenic,
                cost_balanced=cost_balanced,
                scenic_rating=scenic_rating,
                curvature=curvature,
                region=region,
                is_active=True,
            )

            return segment
        except Exception as e:
            logger.error(f"Error creating segment: {e}")
            return None
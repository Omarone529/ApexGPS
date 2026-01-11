import logging
import os
import re
import time
from typing import Any

import requests
from django.contrib.gis.geos import LineString

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
    ]

    # Italian bounding box
    ITALY_BBOX = (35.5, 6.6, 47.1, 18.5)

    # Italian regions with bounding boxes
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
        "test": (41.88, 12.46, 41.92, 12.50),
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


class OSMQueryBuilder:
    """Builds OSM queries."""

    @staticmethod
    def build_road_query(bbox: tuple[float, float, float, float]) -> str:
        """Build road query for a region."""
        min_lat, min_lon, max_lat, max_lon = bbox

        query = f"""
            [out:json][timeout:300];
            way["highway"]
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
            way["highway"~"primary|secondary|tertiary"]
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
    def parse_tags(tags: dict) -> dict:
        """Parse road tags."""
        parsed = {
            "maxspeed": RoadDataProcessor._parse_maxspeed(tags.get("maxspeed")),
            "oneway": tags.get("oneway") == "yes",
            "surface": tags.get("surface"),
            "lanes": RoadDataProcessor._parse_lanes(tags.get("lanes")),
            "name": tags.get("name", ""),
            "highway": tags.get("highway", "unclassified"),
        }
        return parsed

    @staticmethod
    def _parse_maxspeed(maxspeed_str: str | None) -> int | None:
        """Parse maxspeed."""
        if not maxspeed_str:
            return None

        try:
            maxspeed_str = str(maxspeed_str).lower()
            if "km/h" in maxspeed_str:
                return int(maxspeed_str.replace("km/h", "").strip())
            numbers = re.findall(r"\d+", maxspeed_str)
            return int(numbers[0]) if numbers else None
        except (ValueError, TypeError):
            return None

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
    def create_road_segment(way: dict) -> Any:
        """Create RoadSegment from OSM way."""
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
            length_m = geometry.length * 111000

            from gis_data.models import RoadSegment

            segment = RoadSegment(
                osm_id=way["id"],
                name=parsed_tags["name"],
                highway=parsed_tags["highway"],
                geometry=geometry,
                maxspeed=parsed_tags["maxspeed"],
                oneway=parsed_tags["oneway"],
                surface=parsed_tags["surface"],
                lanes=parsed_tags["lanes"],
                length_m=length_m,
            )

            return segment
        except Exception as e:
            logger.error(f"Error creating segment: {e}")
            return None

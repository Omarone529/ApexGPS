import logging
import os
import re
import time
from math import atan2, cos, radians, sin, sqrt
from typing import Any

import requests
from django.contrib.gis.geos import LineString
from django.core.management.base import BaseCommand
from django.db import transaction

from gis_data.models import RoadSegment

logger = logging.getLogger(__name__)

__all__ = [
    "AreaConfig",
    "OSMQueryBuilder",
    "OSMAPIClient",
    "DataParser",
    "OSMDataProcessor",
    "DatabaseManager",
    "ImportPipeline",
    "Command",
]


class AreaConfig:
    """Configuration for OSM areas."""

    @staticmethod
    def get_osm_url() -> str:
        """Get OSM API URL from environment with default."""
        return os.environ.get("OSM_URL", "https://overpass-api.de/api/interpreter")

    @staticmethod
    def get_highway_types() -> list[str]:
        """Get list of highway types to import."""
        return [
            "motorway",
            "trunk",
            "primary",
            "secondary",
            "tertiary",
            "unclassified",
            "residential",
            "service",
            "living_street",
            "track",
        ]

    @staticmethod
    def get_area_ids() -> dict[str, int]:
        """Get mapping of area names to OSM relation IDs."""
        return {
            # Italy Regions
            "italy": 365331,
            "abruzzo": 41211,
            "basilicata": 40073,
            "calabria": 39938,
            "campania": 39624,
            "emilia-romagna": 42615,
            "friuli-venezia giulia": 44690,
            "lazio": 41722,
            "liguria": 45211,
            "lombardia": 45177,
            "marche": 41488,
            "molise": 41356,
            "piemonte": 44872,
            "puglia": 41097,
            "sardegna": 36481,
            "sicilia": 39115,
            "toscana": 42176,
            "trentino-alto adige": 45687,
            "umbria": 42306,
            "valle d'aosta": 45217,
            "veneto": 43630,
            # Test areas
            "test": 41722,
            "rome": 41722,
            "milan": 45177,
            "florence": 42176,
        }

    @staticmethod
    def normalize_area_name(area_name: str) -> str:
        """Normalize area name for lookup."""
        return area_name.lower().replace(" ", "-")

    @staticmethod
    def validate_area_name(area_name: str) -> tuple[bool, str]:
        """Validate that area name is supported."""
        area_name_norm = AreaConfig.normalize_area_name(area_name)
        area_ids = AreaConfig.get_area_ids()

        if area_name_norm in area_ids:
            return True, ""

        for key in area_ids:
            if area_name_norm in key:
                return True, ""

        available = ", ".join(sorted(area_ids.keys()))
        return False, f"Unknown area: {area_name}. Available: {available}"


class OSMQueryBuilder:
    """Builder for OSM Overpass queries."""

    @staticmethod
    def build_roads_query(area_name: str) -> str:
        """Build Overpass query for roads in given area."""
        area_name_norm = AreaConfig.normalize_area_name(area_name)
        area_ids = AreaConfig.get_area_ids()

        if area_name_norm not in area_ids:
            for key, value in area_ids.items():
                if area_name_norm in key:
                    area_id = value
                    break
            else:
                raise ValueError(f"Unknown area: {area_name}")
        else:
            area_id = area_ids[area_name_norm]

        highway_types = AreaConfig.get_highway_types()
        highway_filter = "|".join(highway_types)

        return f"""
            [out:json][timeout:900];
            area({area_id})->.searchArea;
            (
                way["highway"]["highway"~"^{highway_filter}$"](area.searchArea);
            );
            (._;>;);
            out body;
            out skel qt;
        """


class OSMAPIClient:
    """Client for making requests to OSM Overpass API."""

    def __init__(self):
        """Initialize the OSMAPIClient."""
        self.url = AreaConfig.get_osm_url()

    def fetch_roads_data(self, area_name: str) -> dict[str, Any] | None:
        """Fetch road data from OSM for given area."""
        try:
            query = OSMQueryBuilder.build_roads_query(area_name)
            logger.info(f"Query built ({len(query)} chars)")
        except ValueError as e:
            logger.error(f"Query building failed: {e}")
            return None

        return self._make_request(query)

    def _make_request(self, query: str, max_retries: int = 3) -> dict[str, Any] | None:
        """Make request to Overpass API with retries."""
        for attempt in range(max_retries):
            try:
                logger.info(f"OSM request attempt {attempt + 1}/{max_retries}")

                response = requests.post(
                    self.url,
                    data={"data": query},
                    timeout=300,
                    headers={"User-Agent": "ApexGPS/1.0"},
                )
                response.raise_for_status()

                return response.json()

            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 30
                    logger.warning(f"Timeout, waiting {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    logger.error("All requests timed out")

            except requests.exceptions.RequestException as e:
                logger.error(f"Request error: {e}")
                if attempt < max_retries - 1:
                    time.sleep(30)

        return None


class DataParser:
    """Parser for OSM data and tags."""

    @staticmethod
    def parse_maxspeed(maxspeed_str: str | None) -> int | None:
        """Parse maxspeed string to integer km/h."""
        if not maxspeed_str:
            return None

        try:
            maxspeed_str = str(maxspeed_str).lower()

            if "km/h" in maxspeed_str:
                return int(maxspeed_str.replace("km/h", "").strip())
            elif "mph" in maxspeed_str:
                mph = int(maxspeed_str.replace("mph", "").strip())
                return int(mph * 1.60934)
            elif "knots" in maxspeed_str:
                knots = int(maxspeed_str.replace("knots", "").strip())
                return int(knots * 1.852)
            else:
                numbers = re.findall(r"\d+", maxspeed_str)
                if numbers:
                    return int(numbers[0])
                return None
        except (ValueError, TypeError):
            return None

    @staticmethod
    def parse_oneway(oneway_str: str | None) -> bool:
        """Parse oneway string to boolean."""
        if not oneway_str:
            return False

        oneway_str = str(oneway_str).lower()
        return oneway_str in ["yes", "true", "1", "-1", "y"]

    @staticmethod
    def parse_lanes(lanes_str: str | None) -> int | None:
        """Parse lanes string to integer."""
        if not lanes_str:
            return None

        try:
            if "-" in lanes_str:
                parts = lanes_str.split("-")
                return int(parts[0].strip())
            return int(lanes_str)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def haversine_distance(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
        """Calculate distance between two points in meters."""
        R = 6371000  # Earth radius in meters
        lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        return R * c

    @staticmethod
    def calculate_line_length(coords: list[tuple[float, float]]) -> float:
        """Calculate approximate length of coordinate list."""
        if len(coords) < 2:
            return 0.0

        total_length = 0.0
        for i in range(1, len(coords)):
            lon1, lat1 = coords[i - 1]
            lon2, lat2 = coords[i]
            total_length += DataParser.haversine_distance(lon1, lat1, lon2, lat2)

        return total_length


class OSMDataProcessor:
    """Processor for OSM JSON data."""

    def __init__(self):
        """Initialize the OSMDataProcessor."""
        self.nodes: dict[int, tuple[float, float]] = {}
        self.ways: list[dict] = []

    def process_osm_data(self, osm_data: dict[str, Any]) -> None:
        """Extract nodes and ways from OSM data."""
        self.nodes.clear()
        self.ways.clear()

        for element in osm_data.get("elements", []):
            if element["type"] == "node":
                self.nodes[element["id"]] = (element["lon"], element["lat"])
            elif element["type"] == "way":
                self.ways.append(element)

        logger.info(f"Processed {len(self.nodes)} nodes and {len(self.ways)} ways")

    def extract_way_coordinates(self, way: dict) -> list[tuple[float, float]]:
        """Extract coordinates for a way."""
        if "nodes" not in way:
            return []

        coords = []
        for node_id in way["nodes"]:
            if node_id in self.nodes:
                coords.append(self.nodes[node_id])

        return coords

    def is_way_valid(self, way: dict) -> bool:
        """Check if way is valid for processing."""
        if "nodes" not in way or len(way["nodes"]) < 2:
            return False

        tags = way.get("tags", {})
        highway_type = tags.get("highway", "")

        return highway_type in AreaConfig.get_highway_types()

    def create_road_segment(self, way: dict) -> RoadSegment | None:
        """Create RoadSegment from OSM way."""
        if not self.is_way_valid(way):
            return None

        coords = self.extract_way_coordinates(way)
        if len(coords) < 2:
            return None

        try:
            line = LineString(coords, srid=4326)
        except Exception as e:
            logger.debug(f"Failed to create LineString: {e}")
            return None

        tags = way.get("tags", {})
        length_m = DataParser.calculate_line_length(coords)

        return RoadSegment(
            osm_id=way["id"],
            name=tags.get("name", ""),
            highway=tags.get("highway", "unclassified"),
            geometry=line,
            maxspeed=DataParser.parse_maxspeed(tags.get("maxspeed")),
            oneway=DataParser.parse_oneway(tags.get("oneway")),
            surface=tags.get("surface"),
            lanes=DataParser.parse_lanes(tags.get("lanes")),
            length_m=length_m,
        )


class DatabaseManager:
    """Manager for database operations."""

    @staticmethod
    def clear_existing_roads() -> int:
        """Clear all existing road segments."""
        count = RoadSegment.objects.count()
        RoadSegment.objects.all().delete()
        return count

    @staticmethod
    def save_segments_batch(segments: list[RoadSegment], batch_num: int) -> bool:
        """Save a batch of segments to database."""
        if not segments:
            return True

        try:
            RoadSegment.objects.bulk_create(segments, ignore_conflicts=True)
            logger.info(f"Batch {batch_num}: Saved {len(segments)} segments")
            return True
        except Exception as e:
            logger.error(f"Batch {batch_num}: Failed to save - {e}")
            return False

    @staticmethod
    def save_all_segments(segments: list[RoadSegment], batch_size: int = 500) -> int:
        """Save all segments to database in batches."""
        total_saved = 0

        for i in range(0, len(segments), batch_size):
            batch = segments[i : i + batch_size]
            batch_num = i // batch_size + 1

            if DatabaseManager.save_segments_batch(batch, batch_num):
                total_saved += len(batch)

        return total_saved


class ImportPipeline:
    """Main pipeline for importing roads."""

    def __init__(self, area_name: str):
        """Initialize the ImportPipeline."""
        self.area_name = area_name
        self.api_client = OSMAPIClient()
        self.data_processor = OSMDataProcessor()
        self.stats = {
            "fetched_elements": 0,
            "ways_found": 0,
            "segments_created": 0,
            "segments_saved": 0,
            "total_in_db": 0,
        }

    def validate_area(self) -> bool:
        """Validate the area name."""
        is_valid, error_msg = AreaConfig.validate_area_name(self.area_name)
        if not is_valid:
            logger.error(f"Area validation failed: {error_msg}")
            return False
        return True

    def fetch_data(self) -> bool:
        """Fetch data from OSM API."""
        logger.info(f"Fetching data for area: {self.area_name}")

        osm_data = self.api_client.fetch_roads_data(self.area_name)
        if not osm_data:
            logger.error("Failed to fetch data from OSM API")
            return False

        self.stats["fetched_elements"] = len(osm_data.get("elements", []))
        self.data_processor.process_osm_data(osm_data)
        self.stats["ways_found"] = len(self.data_processor.ways)

        return True

    def process_data(self) -> list[RoadSegment]:
        """Process OSM data into road segments."""
        if not self.data_processor.ways:
            logger.error("No ways to process")
            return []

        logger.info(f"Processing {len(self.data_processor.ways)} ways...")

        segments = []
        for i, way in enumerate(self.data_processor.ways):
            segment = self.data_processor.create_road_segment(way)
            if segment:
                segments.append(segment)

            if i > 0 and i % 1000 == 0:
                logger.info(f"Processed {i}/{len(self.data_processor.ways)} ways...")

        self.stats["segments_created"] = len(segments)
        return segments

    def save_to_database(
        self, segments: list[RoadSegment], clear_existing: bool = False
    ) -> bool:
        """Save segments to database."""
        if clear_existing:
            deleted = DatabaseManager.clear_existing_roads()
            logger.info(f"Cleared {deleted} existing road segments")

        if not segments:
            logger.warning("No segments to save")
            return False

        logger.info(f"Saving {len(segments)} segments to database...")

        with transaction.atomic():
            saved_count = DatabaseManager.save_all_segments(segments)

        self.stats["segments_saved"] = saved_count
        self.stats["total_in_db"] = RoadSegment.objects.count()

        return saved_count > 0

    def run(self, clear_existing: bool = False) -> dict:
        """Run the complete import pipeline."""
        result = {"success": False, "error": None, "stats": self.stats.copy()}

        # Validate area
        if not self.validate_area():
            result["error"] = "Invalid area name"
            return result

        # Fetch data
        if not self.fetch_data():
            result["error"] = "Failed to fetch data from OSM"
            return result

        # Process data
        segments = self.process_data()
        if not segments:
            result["error"] = "No valid road segments created"
            return result

        # Save to database
        if not self.save_to_database(segments, clear_existing):
            result["error"] = "Failed to save segments to database"
            return result

        result["success"] = True
        result["stats"] = self.stats.copy()
        return result


class Command(BaseCommand):
    """Import OSM roads command."""

    help = "Import road network from OpenStreetMap"

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            "--area",
            type=str,
            default="test",
            help="Area to import (test, lazio, italy, etc.)",
        )
        parser.add_argument(
            "--clear", action="store_true", help="Clear existing roads before import"
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Batch size for database inserts",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show detailed output",
        )

    def handle(self, *args, **options):
        """Handle command execution."""
        # Setup logging
        if options["verbose"]:
            logging.basicConfig(level=logging.INFO)
        else:
            logging.basicConfig(level=logging.WARNING)
        self.display_header(options["area"])
        self.display_available_areas()
        self.stdout.write(f"\nStarting import for '{options['area']}'...")
        pipeline = ImportPipeline(options["area"])
        result = pipeline.run(clear_existing=options["clear"])
        self.display_results(result)

    def display_header(self, area_name: str):
        """Display command header."""
        self.stdout.write("=" * 60)
        self.stdout.write(f"OSM ROAD IMPORT - Area: {area_name}")
        self.stdout.write("=" * 60)

    def display_available_areas(self):
        """Display available areas."""
        area_ids = AreaConfig.get_area_ids()
        self.stdout.write(f"\nAvailable areas ({len(area_ids)}):")

        areas_list = sorted(area_ids.keys())
        for i in range(0, len(areas_list), 4):
            line_areas = areas_list[i : i + 4]
            self.stdout.write("  " + "  ".join(f"{area:20}" for area in line_areas))

    def display_results(self, result: dict):
        """Display import results."""
        self.stdout.write("\n" + "=" * 60)

        if result["success"]:
            self.stdout.write(self.style.SUCCESS("IMPORT SUCCESSFUL"))
            stats = result["stats"]

            self.stdout.write("\nStatistics:")
            self.stdout.write(f"  Fetched elements: {stats['fetched_elements']:,}")
            self.stdout.write(f"  Ways found: {stats['ways_found']:,}")
            self.stdout.write(f"  Segments created: {stats['segments_created']:,}")
            self.stdout.write(f"  Segments saved: {stats['segments_saved']:,}")
            self.stdout.write(f"  Total in database: {stats['total_in_db']:,}")
        else:
            self.stdout.write(self.style.ERROR("IMPORT FAILED"))
            if result["error"]:
                self.stdout.write(f"\nError: {result['error']}")

            if result["stats"]["fetched_elements"] > 0:
                self.stdout.write("\nPartial statistics:")
                self.stdout.write(
                    f"  Fetched elements: {result['stats']['fetched_elements']:,}"
                )
                self.stdout.write(f"  Ways found: {result['stats']['ways_found']:,}")

        self.stdout.write("=" * 60)

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
    "RegionalItalyImporter",
]


class AreaConfig:
    """Configuration for OSM areas."""

    # Region list with OSM ID
    ITALIAN_REGIONS = {
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
    }

    @staticmethod
    def get_osm_url() -> str:
        """Get OSM API URL from environment with default."""
        return os.environ.get("OSM_URL")

    @staticmethod
    def get_highway_types() -> list[str]:
        """Get list of highway types to import."""
        return [
            "motorway",
            "motorway_link",
            "trunk",
            "trunk_link",
            "primary",
            "primary_link",
            "secondary",
            "secondary_link",
            "tertiary",
            "tertiary_link",
            "unclassified",
            "residential",
            "living_street",
            "service",
            "track",
        ]

    @staticmethod
    def get_area_ids() -> dict[str, int]:
        """Get mapping of area names to OSM relation IDs."""
        regions = AreaConfig.ITALIAN_REGIONS.copy()
        regions.update(
            {
                "italy": 365331,
                "test": 41722,
                "rome": 41722,
                "milan": 45177,
                "florence": 42176,
            }
        )
        return regions

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
    def build_region_query(region_id: int, highway_types: list[str]) -> str:
        """Build optimized query for an Italian region."""
        highway_filter = "|".join(highway_types)

        # Optimized query for italian regions
        query = f"""
            [out:json][timeout:300];
            area({region_id})->.searchArea;
            (
                way["highway"]["highway"~"^({highway_filter})$"](area.searchArea);
            );
            (._;>;);
            out body;
            """
        return query

    @staticmethod
    def build_general_query(area_id: int, highway_types: list[str]) -> str:
        """Build general Overpass query."""
        highway_filter = "|".join(highway_types)

        return f"""
            [out:json][timeout:180];
            area({area_id})->.searchArea;
            (
                way["highway"]["highway"~"^({highway_filter})$"](area.searchArea);
            );
            (._;>;);
            out body;
            """


class RegionalItalyImporter:
    """Importer for Italy by regions."""

    def __init__(self):
        """Initialize the importer."""
        self.api_client = OSMAPIClient()
        self.data_processor = OSMDataProcessor()
        self.total_segments = 0
        self.total_ways = 0
        self.region_stats = {}

    def import_all_regions(self, clear_existing: bool = False) -> dict:
        """Import all Italian regions."""
        if clear_existing:
            deleted = DatabaseManager.clear_existing_roads()
            logger.info(f"Cleared {deleted} existing road segments")

        successful_regions = []
        failed_regions = []

        logger.info(
            f"Starting import of {len(AreaConfig.ITALIAN_REGIONS)} Italian regions..."
        )

        for region_name, region_id in AreaConfig.ITALIAN_REGIONS.items():
            logger.info(f"\n{'=' * 60}")
            logger.info(f"Importing region: {region_name.upper()} (ID: {region_id})")
            logger.info(f"{'=' * 60}")

            result = self._import_region(region_name, region_id)

            if result["success"]:
                successful_regions.append(region_name)
                self.region_stats[region_name] = result
                self.total_segments += result["segments_saved"]
                self.total_ways += result["ways_found"]

                # Pause between regions to avoid rate limiting
                time.sleep(5)
            else:
                failed_regions.append(region_name)
                logger.error(f"Failed to import region {region_name}")

        return {
            "success": len(successful_regions) > 0,
            "successful_regions": successful_regions,
            "failed_regions": failed_regions,
            "total_regions_processed": len(successful_regions),
            "total_segments": self.total_segments,
            "total_ways": self.total_ways,
            "region_stats": self.region_stats,
        }

    def _import_region(self, region_name: str, region_id: int) -> dict:
        """Import a single region."""
        highway_types = AreaConfig.get_highway_types()
        query = OSMQueryBuilder.build_region_query(region_id, highway_types)

        logger.info(f"Fetching data for {region_name}...")

        # Fetch data
        data = None
        for attempt in range(3):
            data = self.api_client._make_request(query)
            if data:
                break
            logger.warning(f"Attempt {attempt + 1} failed for {region_name}")
            time.sleep(30)

        if not data:
            return {
                "success": False,
                "region": region_name,
                "error": "Failed to fetch data",
            }

        # Check for empty response
        elements = data.get("elements", [])
        if not elements:
            logger.warning(f"No elements found for region {region_name}")
            return {
                "success": False,
                "region": region_name,
                "error": "No elements found",
            }

        # Process data
        self.data_processor.process_osm_data(data)
        ways_count = len(self.data_processor.ways)
        nodes_count = len(self.data_processor.nodes)

        logger.info(f"Found {ways_count} ways and {nodes_count} nodes in {region_name}")

        # Create segments - CORREZIONE QUI: usa enumerate invece di contatore separato
        segments = []
        for processed_count, way in enumerate(self.data_processor.ways, 1):  # CORRETTO
            segment = self.data_processor.create_road_segment(way)
            if segment:
                segments.append(segment)

            if processed_count % 1000 == 0:
                logger.info(
                    f"Processed {processed_count}/{ways_count} ways in {region_name}..."
                )

        segments_count = len(segments)

        if not segments:
            logger.warning(f"No valid road segments created for {region_name}")
            return {
                "success": False,
                "region": region_name,
                "error": "No valid segments",
            }

        # Save segments
        saved_count = DatabaseManager.save_all_segments(segments)

        # Clear processor
        self.data_processor.nodes.clear()
        self.data_processor.ways.clear()

        logger.info(
            f"{region_name}: Saved {saved_count} segments (from {ways_count} ways)"
        )

        return {
            "success": True,
            "region": region_name,
            "elements_fetched": len(elements),
            "ways_found": ways_count,
            "segments_created": segments_count,
            "segments_saved": saved_count,
        }


class OSMAPIClient:
    """Client for making requests to OSM Overpass API."""

    def __init__(self):
        """Initialize the OSMAPIClient."""
        self.url = AreaConfig.get_osm_url()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "ApexGPS-Italy-Regional-Importer/1.0",
                "Accept": "application/json",
            }
        )

    def fetch_roads_data(self, area_name: str) -> dict[str, Any] | None:
        """Fetch road data from OSM for given area."""
        try:
            area_name_norm = AreaConfig.normalize_area_name(area_name)
            area_ids = AreaConfig.get_area_ids()

            if area_name_norm not in area_ids:
                raise ValueError(f"Unknown area: {area_name}")

            area_id = area_ids[area_name_norm]
            highway_types = AreaConfig.get_highway_types()

            if area_name_norm == "italy":
                return None
            else:
                query = OSMQueryBuilder.build_general_query(area_id, highway_types)
                logger.info(f"Query built for area: {area_name}")
                return self._make_request(query)
        except ValueError as e:
            logger.error(f"Query building failed: {e}")
            return None

    def _make_request(self, query: str, max_retries: int = 3) -> dict[str, Any] | None:
        """Make request to Overpass API with retries."""
        for attempt in range(max_retries):
            try:
                logger.debug(f"OSM request attempt {attempt + 1}/{max_retries}")

                response = self.session.post(
                    self.url, data={"data": query}, timeout=180
                )

                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 429:
                    # Rate limiting
                    wait_time = (attempt + 1) * 30
                    logger.warning(f"Rate limited. Waiting {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"HTTP {response.status_code}")
                    if attempt < max_retries - 1:
                        time.sleep(30)

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

        elements = osm_data.get("elements", [])
        logger.info(f"Processing {len(elements)} elements...")

        node_count = 0
        way_count = 0

        for element in elements:
            if element["type"] == "node":
                self.nodes[element["id"]] = (element["lon"], element["lat"])
                node_count += 1
            elif element["type"] == "way":
                self.ways.append(element)
                way_count += 1

        logger.info(f"Extracted {node_count} nodes and {way_count} ways")

    def extract_way_coordinates(self, way: dict) -> list[tuple[float, float]]:
        """Extract coordinates for a way."""
        if "nodes" not in way:
            return []

        coords = []
        missing_nodes = 0
        for node_id in way["nodes"]:
            if node_id in self.nodes:
                coords.append(self.nodes[node_id])
            else:
                missing_nodes += 1

        if missing_nodes > 0:
            logger.debug(f"Way {way.get('id')}: {missing_nodes} missing nodes")

        return coords

    def is_way_valid(self, way: dict) -> bool:
        """Check if way is valid for processing."""
        if "nodes" not in way or len(way["nodes"]) < 2:
            return False

        tags = way.get("tags", {})
        highway_type = tags.get("highway", "")

        if not highway_type:
            return False

        valid_types = AreaConfig.get_highway_types()
        return highway_type in valid_types

    def create_road_segment(self, way: dict) -> RoadSegment | None:
        """Create RoadSegment from OSM way."""
        if not self.is_way_valid(way):
            return None

        coords = self.extract_way_coordinates(way)
        if len(coords) < 2:
            logger.debug(f"Way {way.get('id')}: Not enough coordinates ({len(coords)})")
            return None

        try:
            line = LineString(coords, srid=4326)
        except Exception as e:
            logger.debug(f"Failed to create LineString for way {way.get('id')}: {e}")
            return None

        tags = way.get("tags", {})
        length_m = DataParser.calculate_line_length(coords)

        segment = RoadSegment(
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

        return segment


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
    def save_all_segments(segments: list[RoadSegment], batch_size: int = 1000) -> int:
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

        if self.area_name.lower() == "italy":
            return True

        osm_data = self.api_client.fetch_roads_data(self.area_name)
        if not osm_data:
            logger.error("Failed to fetch data from OSM API")
            return False

        self.stats["fetched_elements"] = len(osm_data.get("elements", []))
        self.data_processor.process_osm_data(osm_data)
        self.stats["ways_found"] = len(self.data_processor.ways)

        if self.stats["ways_found"] == 0:
            logger.error("No ways found in the response")
            return False
        return True

    def process_data(self) -> list[RoadSegment]:
        """Process OSM data into road segments."""
        if not self.data_processor.ways:
            logger.error("No ways to process")
            return []

        logger.info(f"Processing {len(self.data_processor.ways)} ways...")

        segments = []
        # CORREZIONE QUI: rinominato i in _ per indicare che non viene usato
        for _, way in enumerate(self.data_processor.ways):
            segment = self.data_processor.create_road_segment(way)
            if segment:
                segments.append(segment)

        self.stats["segments_created"] = len(segments)
        logger.info(f"Created {len(segments)} road segments")
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

        logger.info(f"Successfully saved {saved_count} segments")
        return saved_count > 0

    def run(self, clear_existing: bool = False) -> dict:
        """Run the complete import pipeline."""
        result = {"success": False, "error": None, "stats": self.stats.copy()}

        try:
            # Validate area
            if not self.validate_area():
                result["error"] = "Invalid area name"
                return result

            if self.area_name.lower() == "italy":
                importer = RegionalItalyImporter()
                regional_result = importer.import_all_regions(clear_existing)

                if regional_result["success"]:
                    result["success"] = True
                    result["stats"] = {
                        "total_segments": regional_result["total_segments"],
                        "total_ways": regional_result["total_ways"],
                        "successful_regions": len(
                            regional_result["successful_regions"]
                        ),
                        "failed_regions": len(regional_result["failed_regions"]),
                        "total_in_db": RoadSegment.objects.count(),
                    }
                    result["regional_details"] = regional_result
                else:
                    result["error"] = "Regional import failed"
                return result

            if not self.fetch_data():
                result["error"] = "Failed to fetch data from OSM"
                return result

            segments = self.process_data()
            if not segments:
                result["error"] = "No valid road segments created"
                return result

            if not self.save_to_database(segments, clear_existing):
                result["error"] = "Failed to save segments to database"
                return result

            result["success"] = True
            result["stats"] = self.stats.copy()

        except Exception as e:
            logger.error(f"Import pipeline error: {e}", exc_info=True)
            result["error"] = f"Pipeline error: {str(e)}"
        return result


class Command(BaseCommand):
    """Import OSM roads command."""

    help = "Import road network from OpenStreetMap"

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            "--area",
            type=str,
            default="italy",
            help="Area to import (italy, test, or "
            "specific region like lombardia, lazio, etc.)",
        )
        parser.add_argument(
            "--clear", action="store_true", help="Clear existing roads before import"
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
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
            self.stdout.write("  " + " ".join(f"{area:20}" for area in line_areas))

    def display_results(self, result: dict):
        """Display import results."""
        self.stdout.write("\n" + "=" * 60)

        if result["success"]:
            self.stdout.write(self.style.SUCCESS("IMPORT SUCCESSFUL"))
            stats = result["stats"]

            if "regional_details" in result:
                # Display regional import results
                details = result["regional_details"]
                self.stdout.write("\nRegional Import Summary:")
                self.stdout.write(
                    f"  Successful regions: {len(details['successful_regions'])}"
                )
                self.stdout.write(f"  Failed regions: {len(details['failed_regions'])}")
                self.stdout.write(f"  Total segments: {details['total_segments']:,}")
                self.stdout.write(f"  Total ways: {details['total_ways']:,}")

                if details["successful_regions"]:
                    self.stdout.write("\nSuccessfully imported regions:")
                    for region in sorted(details["successful_regions"]):
                        region_stat = details["region_stats"].get(region, {})
                        segments = region_stat.get("segments_saved", 0)
                        self.stdout.write(f"  - {region}: {segments:,} segments")

                if details["failed_regions"]:
                    self.stdout.write("\nFailed regions:")
                    for region in sorted(details["failed_regions"]):
                        self.stdout.write(f"  - {region}")
            else:
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

        self.stdout.write("=" * 60)

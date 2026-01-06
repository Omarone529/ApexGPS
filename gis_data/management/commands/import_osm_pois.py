import logging
import os
import time

import requests
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand
from django.db import transaction

from gis_data.models import PointOfInterest

logger = logging.getLogger(__name__)

__all__ = [
    "POIConfig",
    "POIQueryBuilder",
    "POIAPIClient",
    "POIDataExtractor",
    "POIDatabaseManager",
    "CategoryProcessor",
    "POIImportPipeline",
    "Command",
]


class POIConfig:
    """Configuration for POI imports."""

    @staticmethod
    def get_osm_url() -> str:
        """Get OSM API URL from environment with default."""
        return os.environ.get("OSM_URL", "https://overpass-api.de/api/interpreter")

    @staticmethod
    def get_area_ids() -> dict[str, int]:
        """Get mapping of area names to OSM relation IDs."""
        return {
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
    def get_available_categories() -> list[str]:
        """Get list of available POI categories."""
        return [
            "viewpoint",
            "panoramic",
            "mountain_pass",
            "lake",
            "historic",
            "castle",
            "church",
            "restaurant",
        ]


class POIQueryBuilder:
    """Builder for POI Overpass queries."""

    QUERIES = {
        "viewpoint": """
            [out:json][timeout:300];
            area({area_id})->.searchArea;
            (
                node["tourism"="viewpoint"](area.searchArea);
                node["amenity"="viewpoint"](area.searchArea);
                way["tourism"="viewpoint"](area.searchArea);
                relation["tourism"="viewpoint"](area.searchArea);
            );
            out center;
            >;
            out skel qt;
        """,
        "panoramic": """
            [out:json][timeout:300];
            area({area_id})->.searchArea;
            (
                node["tourism"="viewpoint"](area.searchArea);
                node["natural"="peak"](area.searchArea);
                way["tourism"="viewpoint"](area.searchArea);
                relation["tourism"="viewpoint"](area.searchArea);
            );
            out center;
            >;
            out skel qt;
        """,
        "mountain_pass": """
            [out:json][timeout:300];
            area({area_id})->.searchArea;
            (
                node["mountain_pass"="yes"](area.searchArea);
                node["natural"="saddle"](area.searchArea);
                node["name"~"Passo|Colle|Pass"](area.searchArea);
                way["mountain_pass"="yes"](area.searchArea);
            );
            out center;
            >;
            out skel qt;
        """,
        "lake": """
            [out:json][timeout:300];
            area({area_id})->.searchArea;
            (
                node["natural"="water"]["water"="lake"](area.searchArea);
                way["natural"="water"]["water"="lake"](area.searchArea);
                relation["natural"="water"]["water"="lake"](area.searchArea);
            );
            out center;
            >;
            out skel qt;
        """,
        "historic": """
            [out:json][timeout:300];
            area({area_id})->.searchArea;
            (
                node["historic"](area.searchArea);
                way["historic"](area.searchArea);
                relation["historic"](area.searchArea);
            );
            out center;
            >;
            out skel qt;
        """,
        "castle": """
            [out:json][timeout:300];
            area({area_id})->.searchArea;
            (
                node["historic"="castle"](area.searchArea);
                node["historic"="fort"](area.searchArea);
                way["historic"="castle"](area.searchArea);
                way["historic"="fort"](area.searchArea);
                relation["historic"="castle"](area.searchArea);
                relation["historic"="fort"](area.searchArea);
            );
            out center;
            >;
            out skel qt;
        """,
        "church": """
            [out:json][timeout:300];
            area({area_id})->.searchArea;
            (
                node["building"="church"](area.searchArea);
                node["amenity"="place_of_worship"](area.searchArea);
                way["building"="church"](area.searchArea);
                way["amenity"="place_of_worship"](area.searchArea);
                relation["building"="church"](area.searchArea);
                relation["amenity"="place_of_worship"](area.searchArea);
            );
            out center;
            >;
            out skel qt;
        """,
        "restaurant": """
            [out:json][timeout:300];
            area({area_id})->.searchArea;
            (
                node["amenity"="restaurant"](area.searchArea);
                node["amenity"="cafe"](area.searchArea);
                way["amenity"="restaurant"](area.searchArea);
                way["amenity"="cafe"](area.searchArea);
                relation["amenity"="restaurant"](area.searchArea);
                relation["amenity"="cafe"](area.searchArea);
            );
            out center;
            >;
            out skel qt;
        """,
    }

    @classmethod
    def build_query(cls, category: str, area_name: str) -> str | None:
        """Build Overpass query for specific category and area."""
        if category not in cls.QUERIES:
            return None

        area_name_norm = POIConfig.normalize_area_name(area_name)
        area_ids = POIConfig.get_area_ids()

        if area_name_norm not in area_ids:
            for key, value in area_ids.items():
                if area_name_norm in key:
                    area_id = value
                    break
            else:
                return None
        else:
            area_id = area_ids[area_name_norm]

        return cls.QUERIES[category].format(area_id=area_id)


class POIAPIClient:
    """Client for making POI requests to OSM API."""

    def __init__(self):
        """Init function."""
        self.url = POIConfig.get_osm_url()

    def fetch_pois(self, category: str, area_name: str) -> list[dict]:
        """Fetch POIs for given category and area."""
        query = POIQueryBuilder.build_query(category, area_name)
        if not query:
            logger.error(f"No query for category: {category}")
            return []

        return self._make_request(query)

    def _make_request(self, query: str, max_retries: int = 3) -> list[dict]:
        """Make request to Overpass API with retries."""
        for attempt in range(max_retries):
            try:
                logger.info(f"POI request attempt {attempt + 1}/{max_retries}")

                response = requests.post(
                    self.url,
                    data={"data": query},
                    timeout=300,
                    headers={"User-Agent": "ApexGPS/1.0"},
                )
                response.raise_for_status()

                data = response.json()
                return data.get("elements", [])

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

        return []


class POIDataExtractor:
    """Extractor for POI data from OSM elements."""

    @staticmethod
    def extract_coordinates(element: dict) -> tuple[float | None, float | None]:
        """Extract coordinates from OSM element."""
        try:
            if element.get("type") == "node":
                lat = element.get("lat")
                lon = element.get("lon")
            elif element.get("center"):
                lat = element.get("center", {}).get("lat")
                lon = element.get("center", {}).get("lon")
            elif element.get("lat") and element.get("lon"):
                lat = element.get("lat")
                lon = element.get("lon")
            else:
                return None, None

            if lat is None or lon is None:
                return None, None

            # Validate coordinates
            if not (-90 <= float(lat) <= 90) or not (-180 <= float(lon) <= 180):
                return None, None

            return float(lat), float(lon)
        except (ValueError, TypeError, KeyError):
            return None, None

    @staticmethod
    def extract_name(tags: dict, category: str, element_id: int) -> str:
        """Extract name from OSM tags."""
        name = tags.get("name") or tags.get("description") or f"{category}_{element_id}"
        return str(name)[:255]

    @staticmethod
    def extract_description(tags: dict) -> str:
        """Extract description from OSM tags."""
        description_parts = []

        if tags.get("description"):
            description_parts.append(str(tags["description"])[:200])

        if tags.get("ele"):
            try:
                elevation = float(tags["ele"])
                description_parts.append(f"Elevation: {elevation:.0f}m")
            except (ValueError, TypeError):
                pass

        if tags.get("wikidata"):
            description_parts.append(f"Wikidata: {tags['wikidata']}")

        return " | ".join(description_parts) if description_parts else ""

    @staticmethod
    def parse_elevation(elevation_str: str | None) -> float | None:
        """Parse elevation string to float."""
        if not elevation_str:
            return None

        try:
            return float(elevation_str)
        except (ValueError, TypeError):
            return None

    def create_poi_data(self, element: dict, category: str) -> dict | None:
        """Create POI data dictionary from OSM element."""
        lat, lon = self.extract_coordinates(element)
        if not lat or not lon:
            return None

        tags = element.get("tags", {})

        return {
            "name": self.extract_name(tags, category, element.get("id", 0)),
            "category": category,
            "location": Point(float(lon), float(lat), srid=4326),
            "description": self.extract_description(tags)[:500],
            "osm_id": element.get("id"),
            "elevation": self.parse_elevation(tags.get("ele")),
            "tags": tags,
        }


class POIDatabaseManager:
    """Manager for POI database operations."""

    @staticmethod
    def clear_existing_pois() -> int:
        """Clear all existing POIs."""
        count = PointOfInterest.objects.count()
        PointOfInterest.objects.all().delete()
        return count

    @staticmethod
    def find_existing_poi(poi_data: dict) -> PointOfInterest | None:
        """Find existing POI by osm_id or location."""
        osm_id = poi_data.get("osm_id")

        # Try by OSM ID first
        if osm_id:
            existing = PointOfInterest.objects.filter(osm_id=osm_id).first()
            if existing:
                return existing

        # Try by location proximity
        location = poi_data["location"]
        existing = PointOfInterest.objects.filter(
            location__dwithin=(location, 0.001)  # ~100m
        ).first()

        return existing

    @staticmethod
    def save_poi(poi_data: dict) -> bool:
        """Save POI to database."""
        try:
            existing = POIDatabaseManager.find_existing_poi(poi_data)

            if existing:
                # Update existing POI
                existing.name = poi_data["name"]
                existing.category = poi_data["category"]
                existing.description = poi_data["description"]
                existing.elevation = poi_data["elevation"]
                existing.tags = poi_data["tags"]
                existing.save()
            else:
                # Create new POI
                PointOfInterest.objects.create(**poi_data)

            return True
        except Exception as e:
            logger.debug(f"Failed to save POI: {e}")
            return False


class CategoryProcessor:
    """Processor for a single POI category."""

    def __init__(self, category: str, area_name: str):
        """Init function."""
        self.category = category
        self.area_name = area_name
        self.api_client = POIAPIClient()
        self.data_extractor = POIDataExtractor()
        self.stats = {
            "elements_fetched": 0,
            "pois_created": 0,
            "pois_saved": 0,
        }

    def fetch_elements(self) -> list[dict]:
        """Fetch elements for this category."""
        logger.info(f"Fetching {self.category} POIs for {self.area_name}")
        elements = self.api_client.fetch_pois(self.category, self.area_name)
        self.stats["elements_fetched"] = len(elements)
        return elements

    def process_elements(self, elements: list[dict], limit: int | None = None) -> int:
        """Process elements and save to database."""
        if not elements:
            return 0

        if limit:
            elements = elements[:limit]

        saved_count = 0
        for i, element in enumerate(elements):
            poi_data = self.data_extractor.create_poi_data(element, self.category)
            if not poi_data:
                continue

            if POIDatabaseManager.save_poi(poi_data):
                saved_count += 1

            if i > 0 and i % 100 == 0:
                logger.info(f"Processed {i}/{len(elements)} elements...")

        self.stats["pois_saved"] = saved_count
        return saved_count

    def run(self, limit: int | None = None) -> dict:
        """Run processing for this category."""
        result = {"success": False, "error": None, "stats": self.stats.copy()}

        # Fetch elements
        elements = self.fetch_elements()
        if not elements:
            result["error"] = f"No elements found for category {self.category}"
            return result

        # Process elements
        saved_count = self.process_elements(elements, limit)
        if saved_count == 0:
            result["error"] = f"No POIs saved for category {self.category}"
            return result

        result["success"] = True
        result["stats"] = self.stats.copy()
        return result


class POIImportPipeline:
    """Main pipeline for importing POIs."""

    def __init__(self, area_name: str):
        """Init function."""
        self.area_name = area_name
        self.category_processors = []
        self.stats = {
            "categories_processed": 0,
            "total_elements_fetched": 0,
            "total_pois_saved": 0,
        }

    def parse_categories_arg(self, categories_arg: str) -> list[str]:
        """Parse categories argument string."""
        if categories_arg == "all":
            return POIConfig.get_available_categories()

        categories = []
        for cat in categories_arg.split(","):
            cat = cat.strip()
            if cat and cat in POIConfig.get_available_categories():
                categories.append(cat)

        return categories

    def create_category_processor(self, category: str) -> CategoryProcessor:
        """Create a processor for a category."""
        return CategoryProcessor(category, self.area_name)

    def process_category(self, category: str, limit: int | None = None) -> dict:
        """Process a single category."""
        logger.info(f"\nProcessing category: {category}")

        processor = self.create_category_processor(category)
        result = processor.run(limit)

        # Update overall statistics
        self.stats["categories_processed"] += 1
        self.stats["total_elements_fetched"] += result["stats"]["elements_fetched"]
        self.stats["total_pois_saved"] += result["stats"]["pois_saved"]

        return result

    def run(
        self,
        categories: list[str],
        limit: int | None = None,
        clear_existing: bool = False,
    ) -> dict:
        """Run the complete POI import pipeline."""
        result = {"success": False, "errors": [], "stats": self.stats.copy()}

        # Clear existing if requested
        if clear_existing:
            deleted = POIDatabaseManager.clear_existing_pois()
            logger.info(f"Cleared {deleted} existing POIs")

        # Process each category
        for category in categories:
            category_result = self.process_category(category, limit)

            if not category_result["success"]:
                result["errors"].append(category_result["error"])

        # Determine overall success
        if self.stats["total_pois_saved"] > 0:
            result["success"] = True
        else:
            result["success"] = False

        result["stats"] = self.stats.copy()
        return result


class Command(BaseCommand):
    """Management command for importing Points of Interest from OpenStreetMap."""

    help = "Import Points of Interest from OpenStreetMap"

    def add_arguments(self, parser):
        """Define command-line arguments for POI import."""
        parser.add_argument(
            "--area",
            type=str,
            default="test",
            help="Geographic area to search (e.g., 'test', 'lazio', 'italy')",
        )
        parser.add_argument(
            "--categories",
            type=str,
            default="viewpoint",
            help="Comma-separated list of categories to import or 'all'",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear existing POIs before import",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=100,
            help="Limit number of POIs per category (0 for no limit)",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show detailed output",
        )

    def setup_logging(self, verbose: bool):
        """Setup logging based on verbosity."""
        if verbose:
            logging.basicConfig(level=logging.INFO)
        else:
            logging.basicConfig(level=logging.WARNING)

    def display_header(self, area: str, categories: list[str]):
        """Display command header."""
        self.stdout.write("=" * 60)
        self.stdout.write(f"OSM POI IMPORT - Area: {area}")
        self.stdout.write("=" * 60)

        self.stdout.write(f"\nCategories to import: {', '.join(categories)}")
        if self.options.get("limit"):
            self.stdout.write(f"Limit per category: {self.options['limit']}")

    def display_available_categories(self):
        """Display available categories."""
        categories = POIConfig.get_available_categories()
        self.stdout.write(f"\nAvailable categories ({len(categories)}):")

        for i in range(0, len(categories), 3):
            line_cats = categories[i : i + 3]
            self.stdout.write("  " + "  ".join(f"{cat:20}" for cat in line_cats))

    def display_available_areas(self):
        """Display available areas."""
        area_ids = POIConfig.get_area_ids()
        self.stdout.write(f"\nAvailable areas ({len(area_ids)}):")

        areas_list = sorted(area_ids.keys())
        for i in range(0, len(areas_list), 4):
            line_areas = areas_list[i : i + 4]
            self.stdout.write("  " + "  ".join(f"{area:20}" for area in line_areas))

    def display_results(self, result: dict):
        """Display import results."""
        self.stdout.write("\n" + "=" * 60)

        if result["success"]:
            self.stdout.write(self.style.SUCCESS("POI IMPORT SUCCESSFUL"))

            stats = result["stats"]
            self.stdout.write("\nStatistics:")
            self.stdout.write(
                f"  Categories processed: {stats['categories_processed']}"
            )
            self.stdout.write(
                f"  Elements fetched: {stats['total_elements_fetched']:,}"
            )
            self.stdout.write(f"  POIs saved: {stats['total_pois_saved']:,}")
            self.stdout.write(
                f"  Total in database: {PointOfInterest.objects.count():,}"
            )
        else:
            self.stdout.write(self.style.ERROR("POI IMPORT FAILED"))

            if result["errors"]:
                self.stdout.write("\nErrors:")
                for error in result["errors"]:
                    self.stdout.write(f"  - {error}")

            if result["stats"]["total_pois_saved"] > 0:
                self.stdout.write("\nPartial statistics:")
                self.stdout.write(
                    f"  POIs saved: {result['stats']['total_pois_saved']:,}"
                )

        self.stdout.write("=" * 60)

    def handle(self, *args, **options):
        """Execute the OSM POI import process."""
        self.options = options

        # Setup logging
        self.setup_logging(options["verbose"])

        # Parse categories
        pipeline = POIImportPipeline(options["area"])
        categories = pipeline.parse_categories_arg(options["categories"])

        if not categories:
            self.stdout.write(self.style.ERROR("No valid categories specified"))
            return

        # Display information
        self.display_header(options["area"], categories)
        self.display_available_categories()
        self.display_available_areas()

        # Run import pipeline
        self.stdout.write("\nStarting import...")

        with transaction.atomic():
            result = pipeline.run(
                categories=categories,
                limit=options["limit"] if options["limit"] > 0 else None,
                clear_existing=options["clear"],
            )

        # Display results
        self.display_results(result)

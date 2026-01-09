import logging
import os
import re
import threading
import time
from urllib.parse import urlparse

import requests
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from gis_data.models import PointOfInterest

logger = logging.getLogger(__name__)

__all__ = [
    "EnvironmentConfiguration",
    "QueryConstructor",
    "APIClient",
    "EndpointManager",
    "RateLimiter",
    "POIDataParser",
    "DatabaseOperations",
    "CategoryImportExecutor",
    "ImportOrchestrator",
    "Command",
]


class EnvironmentConfiguration:
    """Handles environment variable configuration."""

    ENDPOINT_VARIABLES = [
        "OSM_URL",
        "OSM_URL_KUMI",
        "OSM_URL_NCHC",
        "OSM_URL_CH",
    ]

    @classmethod
    def get_environment_endpoints(cls) -> list[str]:
        """Retrieve all valid endpoints from environment variables."""
        endpoints = []

        for var_name in cls.ENDPOINT_VARIABLES:
            endpoint = cls._parse_endpoint_variable(var_name)
            if endpoint:
                endpoints.append(endpoint)

        return endpoints

    @classmethod
    def _parse_endpoint_variable(cls, var_name: str) -> str | None:
        """Parse a single environment variable for an endpoint URL."""
        raw_value = os.environ.get(var_name)
        if not raw_value:
            return None

        endpoint = cls._extract_url(raw_value)
        if endpoint and cls._is_valid_url(endpoint):
            logger.debug(f"Found endpoint in {var_name}: {endpoint}")
            return endpoint

        logger.warning(f"Invalid endpoint in {var_name}")
        return None

    @staticmethod
    def _extract_url(text: str) -> str | None:
        """Extract first URL from text."""
        # Simple URL pattern matching
        url_pattern = r'https?://[^\s<>"\'{}|\\^`\[\]]+'
        match = re.search(url_pattern, text)
        return match.group(0) if match else None

    @staticmethod
    def _is_valid_url(url: str) -> bool:
        """Validate URL format."""
        try:
            result = urlparse(url)
            return all([result.scheme in ("http", "https"), result.netloc])
        except Exception:
            return False


class QueryConstructor:
    """Constructs Overpass API queries."""

    CATEGORY_MAPPING = {
        "viewpoint": [("tourism", "viewpoint")],
        "restaurant": [("amenity", "restaurant")],
        "church": [("building", "church")],
        "historic": [("historic", "archaeological_site"), ("historic", "castle")],
    }

    @classmethod
    def build_query(cls, category: str, bbox: str) -> str:
        """Build Overpass query for a category."""
        if category not in cls.CATEGORY_MAPPING:
            raise ValueError(f"Unknown category: {category}")

        tag_conditions = cls.CATEGORY_MAPPING[category]
        query_parts = []

        for tag_key, tag_value in tag_conditions:
            query_parts.append(f'node["{tag_key}"="{tag_value}"]({bbox});')
            query_parts.append(f'way["{tag_key}"="{tag_value}"]({bbox});')

        query_body = "\n  ".join(query_parts)

        # Simplified query from documentation
        query = f"""[out:json][timeout:180];
        (
          {query_body}
        );
        out center;
        """

        return query


class APIClient:
    """HTTP client for Overpass API requests."""

    def __init__(self):
        """Initialize client."""
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "ApexGPS-POI-Importer/1.0",
                "Accept": "application/json",
            }
        )

    def execute_query(
        self, endpoint: str, query: str, timeout: int = 180
    ) -> dict | None:
        """Execute Overpass query and return parsed JSON."""
        for attempt in range(3):
            try:
                response = self.session.post(
                    endpoint, data={"data": query}, timeout=timeout
                )

                if response.status_code == 200:
                    return response.json()
                else:
                    logger.warning(f"HTTP {response.status_code} from {endpoint}")

            except requests.exceptions.Timeout:
                logger.warning(f"Timeout from {endpoint} (attempt {attempt + 1}/3)")
                if attempt < 2:
                    time.sleep(30)
                    continue
                return None
            except requests.exceptions.RequestException as e:
                logger.warning(f"Request error from {endpoint}: {e}")
                if attempt < 2:
                    time.sleep(30)
                    continue
                return None

        return None


class EndpointManager:
    """Manages OSM endpoint selection and failover."""

    DEFAULT_ENDPOINTS = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.openstreetmap.fr/api/interpreter",
    ]

    def __init__(self):
        """Initialize endpoint manager."""
        self.endpoints = self._gather_endpoints()
        self.current_index = 0

    def _gather_endpoints(self) -> list[str]:
        """Gather all available endpoints."""
        endpoints = EnvironmentConfiguration.get_environment_endpoints()

        if not endpoints:
            logger.info("Using default endpoints")
            endpoints = self.DEFAULT_ENDPOINTS

        logger.info(f"Available endpoints: {len(endpoints)}")
        return endpoints

    def get_next_endpoint(self) -> str:
        """Get next endpoint in round-robin fashion."""
        if not self.endpoints:
            raise ValueError("No endpoints available")

        endpoint = self.endpoints[self.current_index]
        self.current_index = (self.current_index + 1) % len(self.endpoints)
        return endpoint


class RateLimiter:
    """Simple rate limiter for API calls."""

    def __init__(self, requests_per_minute: int = 5):
        """Initialize RateLimiter."""
        self.delay = 60.0 / requests_per_minute
        self.last_call = 0
        self.lock = threading.Lock()

    def wait(self):
        """Wait if needed to respect rate limits."""
        with self.lock:
            now = time.time()
            elapsed = now - self.last_call

            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)

            self.last_call = time.time()


class POIDataParser:
    """Parses OSM element data into POI objects."""

    @staticmethod
    def parse_element(element: dict, category: str) -> dict | None:
        """Parse OSM element into POI dictionary."""
        try:
            # Extract coordinates
            coords = POIDataParser._get_coordinates(element)
            if not coords:
                return None

            tags = element.get("tags", {})

            return {
                "osm_id": element.get("id"),
                "name": POIDataParser._get_name(tags, category, element.get("id", 0)),
                "category": category,
                "location": Point(coords[0], coords[1], srid=4326),
                "description": POIDataParser._get_description(tags, category),
                "tags": tags,
            }
        except Exception as e:
            logger.debug(f"Failed to parse element: {e}")
            return None

    @staticmethod
    def _get_coordinates(element: dict) -> tuple | None:
        """Extract coordinates from OSM element."""
        # Node coordinates
        if element.get("type") == "node":
            lat = element.get("lat")
            lon = element.get("lon")
            if lat and lon:
                return float(lon), float(lat)

        # Center coordinates for ways/relations
        center = element.get("center", {})
        lat = center.get("lat")
        lon = center.get("lon")
        if lat and lon:
            return float(lon), float(lat)

        return None

    @staticmethod
    def _get_name(tags: dict, category: str, element_id: int) -> str:
        """Extract name from tags."""
        name = tags.get("name")
        if name:
            return str(name)[:200]

        description = tags.get("description")
        if description:
            return str(description)[:200]

        return f"{category.title()} #{element_id}"

    @staticmethod
    def _get_description(tags: dict, category: str) -> str:
        """Create description from tags."""
        parts = []

        if tags.get("description"):
            parts.append(tags["description"][:150])

        if tags.get("historic"):
            parts.append(f"Historic: {tags['historic']}")

        if tags.get("amenity"):
            parts.append(f"Amenity: {tags['amenity']}")

        return " | ".join(parts) if parts else f"{category.replace('_', ' ').title()}"


class DatabaseOperations:
    """Handles database operations for POIs."""

    @staticmethod
    @transaction.atomic
    def save_category_pois(pois: list[dict], category: str) -> int:
        """Save POIs for a specific category."""
        if not pois:
            return 0

        # Clear existing category POIs
        deleted = PointOfInterest.objects.filter(category=category).delete()[0]
        if deleted:
            logger.info(f"Cleared {deleted} existing {category} POIs")

        # Create new POIs
        poi_objects = []
        for poi_data in pois:
            poi_objects.append(PointOfInterest(**poi_data))

        try:
            saved = PointOfInterest.objects.bulk_create(
                poi_objects, ignore_conflicts=True, batch_size=500
            )
            logger.info(f"Saved {len(saved)} {category} POIs")
            return len(saved)
        except Exception as e:
            logger.error(f"Failed to save {category} POIs: {e}")
            return 0

    @staticmethod
    def get_database_summary() -> dict:
        """Get database statistics."""
        total = PointOfInterest.objects.count()

        category_stats = (
            PointOfInterest.objects.values("category")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        return {
            "total": total,
            "categories": list(category_stats),
        }


class CategoryImportExecutor:
    """Executes import for a single category."""

    def __init__(self, category: str, area_name: str):
        """Initialize category import."""
        self.category = category
        self.area_name = area_name
        self.endpoint_manager = EndpointManager()
        self.api_client = APIClient()
        self.rate_limiter = RateLimiter()
        self.parser = POIDataParser()

    def execute(self) -> dict:
        """Execute import for this category."""
        logger.info(f"Importing {self.category} for {self.area_name}")

        if self.area_name == "italy":
            bbox = "35.5,6.6,47.1,18.5"
        else:
            # Per area test (Roma)
            bbox = "41.89,12.47,41.91,12.49"

        # Build query
        query = QueryConstructor.build_query(self.category, bbox)

        # Try each endpoint
        for _ in range(len(self.endpoint_manager.endpoints)):
            self.rate_limiter.wait()

            endpoint = self.endpoint_manager.get_next_endpoint()
            logger.info(f"Trying {endpoint} for {self.category}")

            data = self.api_client.execute_query(endpoint, query)
            if not data:
                continue

            elements = data.get("elements", [])
            if not elements:
                logger.warning(f"No {self.category} elements found")
                continue

            # Parse elements
            pois = []
            for element in elements:
                poi = self.parser.parse_element(element, self.category)
                if poi:
                    pois.append(poi)

            if not pois:
                logger.warning(f"No valid {self.category} POIs created")
                continue

            # Save to database
            saved = DatabaseOperations.save_category_pois(pois, self.category)

            return {
                "success": True,
                "category": self.category,
                "elements": len(elements),
                "pois_created": len(pois),
                "pois_saved": saved,
            }

        return {
            "success": False,
            "category": self.category,
            "error": "All endpoints failed",
        }


class ImportOrchestrator:
    """Orchestrates the import of multiple categories."""

    def __init__(self, categories: list[str], area_name: str):
        """Initialize orchestrator."""
        self.categories = categories
        self.area_name = area_name

    def run(self) -> dict:
        """Run import for all categories."""
        logger.info(f"Starting POI import for {self.area_name}")
        start_time = time.time()

        results = []
        stats = {
            "categories": 0,
            "elements": 0,
            "pois_created": 0,
            "pois_saved": 0,
        }

        for category in self.categories:
            executor = CategoryImportExecutor(category, self.area_name)
            result = executor.execute()
            results.append(result)

            if result["success"]:
                stats["categories"] += 1
                stats["elements"] += result["elements"]
                stats["pois_created"] += result["pois_created"]
                stats["pois_saved"] += result["pois_saved"]

        elapsed = time.time() - start_time

        return {
            "success": stats["pois_saved"] > 0,
            "statistics": stats,
            "results": results,
            "duration": elapsed,
        }


class Command(BaseCommand):
    """Django management command for OSM POI import."""

    help = "Import Points of Interest from OpenStreetMap"

    def add_arguments(self, parser):
        """Adds arguments required for the command."""
        parser.add_argument(
            "--area",
            default="italy",
            help="Area name for import",
        )
        parser.add_argument(
            "--categories",
            default="viewpoint,restaurant,church,historic",
            help="Comma-separated categories to import",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Enable verbose output",
        )

    def handle(self, *args, **options):
        """Execute the command."""
        self._setup_logging(options["verbose"])

        # Parse categories
        categories = self._parse_categories(options["categories"])
        if not categories:
            self.stdout.write("Error: No valid categories specified")
            return

        # Display configuration
        self._print_header(options["area"], categories)

        # Execute import
        orchestrator = ImportOrchestrator(categories, options["area"])
        result = orchestrator.run()

        # Display results
        self._print_results(result)

    def _setup_logging(self, verbose: bool):
        """Configure logging level."""
        level = logging.DEBUG if verbose else logging.INFO
        logging.basicConfig(
            level=level,
            format="%(asctime)s - %(levelname)s - %(message)s",
        )

    def _parse_categories(self, categories_str: str) -> list[str]:
        """Parse categories string into list."""
        valid_categories = {"viewpoint", "restaurant", "church", "historic"}

        categories = []
        for cat in categories_str.split(","):
            cat = cat.strip().lower()
            if cat in valid_categories:
                categories.append(cat)

        return categories

    def _print_header(self, area: str, categories: list[str]):
        """Print command header."""
        self.stdout.write("=" * 60)
        self.stdout.write("OSM POI IMPORT")
        self.stdout.write("=" * 60)
        self.stdout.write(f"Area: {area}")
        self.stdout.write(f"Categories: {', '.join(categories)}")
        self.stdout.write("=" * 60)
        self.stdout.write("")

    def _print_results(self, result: dict):
        """Print import results."""
        self.stdout.write("=" * 60)

        if result["success"]:
            self.stdout.write("IMPORT SUCCESSFUL")
        else:
            self.stdout.write("IMPORT FAILED")

        stats = result["statistics"]
        self.stdout.write("")
        self.stdout.write("Statistics:")
        self.stdout.write(f"  Categories processed: {stats['categories']}")
        self.stdout.write(f"  Elements fetched:     {stats['elements']:,}")
        self.stdout.write(f"  POIs created:         {stats['pois_created']:,}")
        self.stdout.write(f"  POIs saved:           {stats['pois_saved']:,}")
        self.stdout.write(f"  Duration:             {result['duration']:.1f}s")

        # Database summary
        db_summary = DatabaseOperations.get_database_summary()
        self.stdout.write("")
        self.stdout.write("Database:")
        self.stdout.write(f"  Total POIs:          {db_summary['total']:,}")

        if db_summary["categories"]:
            self.stdout.write("")
            self.stdout.write("Category breakdown:")
            for category in db_summary["categories"]:
                percent = (
                    (category["count"] / db_summary["total"] * 100)
                    if db_summary["total"] > 0
                    else 0
                )
                self.stdout.write(
                    f"  {category['category']:20} {category['count']:6,}"
                    f" ({percent:.1f}%)"
                )

        errors = [r for r in result["results"] if not r.get("success")]
        if errors:
            self.stdout.write("")
            self.stdout.write("Errors:")
            for error in errors:
                self.stdout.write(
                    f"  - {error['category']}: {error.get('error', 'Unknown error')}"
                )

        self.stdout.write("=" * 60)

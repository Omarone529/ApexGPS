import logging
import os
import re
import threading
import time
from typing import Any

import requests
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from gis_data.models import PointOfInterest

logger = logging.getLogger(__name__)

__all__ = [
    "POIConfig",
    "POIQueryBuilder",
    "POIRateLimiter",
    "POIAPIClient",
    "POIDataParser",
    "POIDataProcessor",
    "POIDatabaseManager",
    "POIImportPipeline",
    "Command",
]


class POIConfig:
    """Configuration for OSM POI imports."""

    @staticmethod
    def get_osm_endpoints() -> list[str]:
        """Get OSM API endpoints from environment variables."""
        endpoints = []

        endpoint_vars = [
            "OSM_URL",
            "OSM_URL_KUMI",
            "OSM_URL_NCHC",
            "OSM_URL_CH",
        ]

        for var_name in endpoint_vars:
            endpoint = os.environ.get(var_name, "").strip()

            # check if it starts with http:// or https://
            if endpoint and endpoint.startswith(
                ("http://", "https://") and endpoint not in endpoints
            ):
                endpoints.append(endpoint)

        if endpoints:
            logger.info(f"Using {len(endpoints)} OSM endpoints")
            return endpoints

        return [
            "https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
        ]

    @staticmethod
    def get_poi_categories() -> dict[str, list[tuple[str, str]]]:
        """Get POI categories with their OSM tag combinations."""
        return {
            "viewpoint": [
                ("tourism", "viewpoint"),
                ("amenity", "viewpoint"),
                ("natural", "peak"),
                ("man_made", "tower"),
            ],
            "panoramic": [
                ("tourism", "viewpoint"),
                ("natural", "peak"),
                ("man_made", "observation_tower"),
            ],
            "mountain_pass": [
                ("mountain_pass", "yes"),
                ("natural", "saddle"),
            ],
            "lake": [
                ("natural", "water"),
                ("water", "lake"),
                ("landuse", "basin"),
            ],
            "historic": [
                ("historic", "archaeological_site"),
                ("historic", "castle"),
                ("historic", "fort"),
                ("historic", "monument"),
                ("historic", "ruins"),
                ("historic", "tomb"),
                ("historic", "wayside_cross"),
                ("historic", "wayside_shrine"),
            ],
            "castle": [
                ("historic", "castle"),
                ("historic", "fort"),
                ("building", "castle"),
            ],
            "church": [
                ("building", "church"),
                ("building", "chapel"),
                ("building", "cathedral"),
                ("amenity", "place_of_worship"),
                ("historic", "church"),
            ],
            "restaurant": [
                ("amenity", "restaurant"),
                ("amenity", "cafe"),
                ("amenity", "bar"),
                ("amenity", "fast_food"),
                ("amenity", "food_court"),
            ],
            "park": [
                ("leisure", "park"),
                ("leisure", "garden"),
                ("tourism", "theme_park"),
            ],
            "beach": [
                ("natural", "beach"),
                ("leisure", "beach_resort"),
            ],
        }

    @staticmethod
    def get_area_ids() -> dict[str, str]:
        """Get mapping of area names to OSM bounding boxes."""
        return {
            # Test areas - small bounding boxes
            "test": "41.89,12.47,41.91,12.49",
            "rome": "41.89,12.47,41.91,12.49",
            "milan": "45.46,9.18,45.47,9.19",
            "florence": "43.77,11.25,43.78,11.26",
            "test-large": "41.85,12.45,41.95,12.55",
            "test-tiny": "41.895,12.475,41.900,12.480",
            "italy": "35.29,6.62,47.10,18.52",
            "abruzzo": "41.55,13.08,42.90,14.85",
            "basilicata": "39.90,15.40,41.35,16.85",
            "calabria": "37.90,15.65,40.30,17.20",
            "campania": "40.00,13.70,41.65,15.95",
            "emilia-romagna": "43.75,9.48,45.10,12.75",
            "friuli-venezia-giulia": "45.60,12.45,46.65,13.90",
            "lazio": "41.20,11.50,42.85,14.00",
            "liguria": "43.75,7.60,44.70,10.00",
            "lombardia": "44.68,8.52,46.62,11.55",
            "marche": "42.65,12.85,43.95,14.05",
            "molise": "41.40,14.00,42.05,15.30",
            "piemonte": "44.05,6.62,46.47,9.32",
            "puglia": "39.80,15.90,42.20,18.52",
            "sardegna": "38.87,8.12,41.25,9.85",
            "sicilia": "36.65,12.42,38.27,15.65",
            "toscana": "42.20,9.70,44.50,12.38",
            "trentino-alto-adige": "45.65,10.40,47.10,12.45",
            "umbria": "42.35,11.90,43.65,13.25",
            "valle-daosta": "45.60,6.85,45.95,7.85",
            "veneto": "44.78,10.58,46.72,13.05",
        }

    @staticmethod
    def normalize_area_name(area_name: str) -> str:
        """Normalize area name for lookup."""
        return area_name.lower().replace(" ", "-")

    @staticmethod
    def validate_area_name(area_name: str) -> tuple[bool, str]:
        """Validate that area name is supported."""
        area_name_norm = POIConfig.normalize_area_name(area_name)
        area_ids = POIConfig.get_area_ids()

        if area_name_norm in area_ids:
            return True, ""

        available = ", ".join(sorted(area_ids.keys()))
        return False, f"Unknown area: {area_name}. Available: {available}"

    @staticmethod
    def get_available_categories() -> list[str]:
        """Get list of available POI categories."""
        return list(POIConfig.get_poi_categories().keys())


class POIQueryBuilder:
    """Builder for OSM Overpass queries for POIs."""

    @staticmethod
    def build_poi_query(category: str, bbox: str) -> str:
        """Build Overpass query for POIs in bounding box."""
        categories = POIConfig.get_poi_categories()

        if category not in categories:
            return ""

        tag_combinations = categories[category]

        # Build query parts for each tag combination
        query_parts = []
        for tag_key, tag_value in tag_combinations:
            query_parts.append(f'node["{tag_key}"="{tag_value}"]({bbox});')
            query_parts.append(f'way["{tag_key}"="{tag_value}"]({bbox});')
            query_parts.append(f'relation["{tag_key}"="{tag_value}"]({bbox});')

        # Combine all query parts
        query_body = "\n  ".join(query_parts)

        # Build the complete query
        query = f"""[out:json][timeout:300];
        (
          {query_body}
        );
        out center;
        >;
        out skel qt;
        """

        logger.debug(f"Generated query for {category}:\n{query}")
        return query

    @staticmethod
    def build_simple_poi_query(category: str, bbox: str) -> str:
        """Build a simpler query for testing."""
        categories = POIConfig.get_poi_categories()

        if category not in categories:
            return ""

        # Use just the first tag combination for simplicity
        tag_key, tag_value = categories[category][0]

        return f"""[out:json][timeout:180];
        node["{tag_key}"="{tag_value}"]({bbox});
        out body;
        """


class POIRateLimiter:
    """Rate limiter for API requests."""

    def __init__(self):
        """Initialize rate limiter."""
        try:
            self.requests_per_minute = int(os.environ.get("OSM_RATE_LIMIT", "10"))
        except ValueError:
            self.requests_per_minute = 10

        self.min_delay = 60.0 / self.requests_per_minute
        self.last_request_time = 0
        self.lock = threading.Lock()

    def wait_if_needed(self):
        """Wait if needed to respect rate limits."""
        with self.lock:
            current_time = time.time()
            time_since_last = current_time - self.last_request_time

            if time_since_last < self.min_delay:
                wait_time = self.min_delay - time_since_last
                time.sleep(wait_time)

            self.last_request_time = time.time()


class POIAPIClient:
    """Client for making requests to OSM Overpass API for POIs."""

    def __init__(self):
        """Initialize client."""
        self.endpoints = POIConfig.get_osm_endpoints()
        self.current_endpoint_index = 0
        self.rate_limiter = POIRateLimiter()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "ApexGPS-POI-Importer/1.0 (https://github.com/apexgps)",
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
            }
        )

    def fetch_pois_for_category(
        self, category: str, area_name: str
    ) -> dict[str, Any] | None:
        """Fetch POI data for a category and area."""
        area_name_norm = POIConfig.normalize_area_name(area_name)
        area_ids = POIConfig.get_area_ids()

        if area_name_norm not in area_ids:
            logger.error(f"Unknown area: {area_name}")
            return None

        bbox = area_ids[area_name_norm]

        # Apply rate limiting
        self.rate_limiter.wait_if_needed()

        # Try simple query first, then regular
        strategies = [
            self._try_simple_query,
            self._try_regular_query,
        ]

        for strategy in strategies:
            data = strategy(category, bbox, area_name)
            if data and data.get("elements"):
                return data

        return None

    def _try_simple_query(
        self, category: str, bbox: str, area_name: str
    ) -> dict[str, Any] | None:
        """Try a simple query first."""
        logger.info(f"Trying simple query for {category} in {area_name}")
        query = POIQueryBuilder.build_simple_poi_query(category, bbox)
        return self._make_request(query, f"simple {category} query for {area_name}")

    def _try_regular_query(
        self, category: str, bbox: str, area_name: str
    ) -> dict[str, Any] | None:
        """Try the regular query."""
        logger.info(f"Trying regular query for {category} in {area_name}")
        query = POIQueryBuilder.build_poi_query(category, bbox)
        return self._make_request(query, f"regular {category} query for {area_name}")

    def _make_request(self, query: str, description: str) -> dict[str, Any] | None:
        """Make a request to Overpass API."""
        if not query:
            logger.error(f"Empty query for {description}")
            return None

        compressed_query = " ".join(query.strip().split())

        try:
            timeout = int(os.environ.get("OSM_TIMEOUT", "300"))
            max_retries = int(os.environ.get("OSM_MAX_RETRIES", "3"))
        except ValueError:
            timeout = 300
            max_retries = 3

        for attempt in range(max_retries):
            endpoint = self.endpoints[self.current_endpoint_index % len(self.endpoints)]
            self.current_endpoint_index += 1

            try:
                logger.info(
                    f"Fetching {description} (attempt {attempt + 1}/{max_retries})"
                )

                response = self.session.post(
                    endpoint,
                    data={"data": compressed_query},
                    timeout=timeout,
                )

                if response.status_code == 200:
                    data = response.json()
                    if "elements" in data:
                        logger.info(
                            f"✓ Successfully fetched {len(data['elements'])} elements"
                        )
                        return data
                    else:
                        logger.warning("Response missing 'elements' key")
                elif response.status_code == 400:
                    logger.warning("Bad request (400) - query syntax may be invalid")
                    logger.debug(f"Problematic query: {query[:500]}...")
                    time.sleep(10)
                elif response.status_code == 504:
                    logger.warning("Gateway timeout (504)")
                    time.sleep(30)
                elif response.status_code == 429:
                    logger.warning("Rate limited (429)")
                    time.sleep(60)
                else:
                    logger.warning(f"Request failed with status {response.status_code}")
                    time.sleep(10)

            except requests.exceptions.Timeout:
                logger.warning(f"Timeout for {description}")
                time.sleep(30)
            except requests.exceptions.RequestException as e:
                logger.warning(f"Request error: {e}")
                time.sleep(10)
            except Exception as e:
                logger.warning(f"Unexpected error: {e}")
                time.sleep(10)

        logger.error(
            f"Failed to fetch data for {description} after {max_retries} attempts"
        )
        return None


class POIDataParser:
    """Parser for OSM POI data and tags."""

    @staticmethod
    def parse_elevation(elevation_str: str | None) -> float | None:
        """Parse elevation string to float."""
        if not elevation_str:
            return None

        try:
            elevation_str = str(elevation_str).lower()
            elevation_str = re.sub(r"[^0-9.\-]", "", elevation_str)
            return float(elevation_str)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def extract_coordinates(element: dict[str, Any]) -> tuple[float, float] | None:
        """Extract coordinates from OSM element."""
        try:
            # Direct lat/lon for nodes
            if element.get("type") == "node":
                lat = element.get("lat")
                lon = element.get("lon")
                if lat is not None and lon is not None:
                    return float(lon), float(lat)

            # Center for ways/relations
            if element.get("center"):
                lat = element.get("center", {}).get("lat")
                lon = element.get("center", {}).get("lon")
                if lat is not None and lon is not None:
                    return float(lon), float(lat)

            # Geometry
            if element.get("geometry"):
                geometry = element.get("geometry", [])
                if geometry and isinstance(geometry, list) and len(geometry) > 0:
                    point = geometry[0]
                    lat = point.get("lat")
                    lon = point.get("lon")
                    if lat is not None and lon is not None:
                        return float(lon), float(lat)

            return None

        except (ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug(f"Error extracting coordinates: {e}")
            return None

    @staticmethod
    def extract_name(tags: dict[str, Any], category: str, element_id: int) -> str:
        """Extract name from OSM tags."""
        name = tags.get("name")
        if name:
            return str(name)[:200]

        # Category-specific fallbacks
        if category == "viewpoint":
            description = (
                tags.get("description") or tags.get("note") or tags.get("information")
            )
            if description:
                return str(description)[:200]

        if category == "mountain_pass":
            pass_name = (
                tags.get("name:en") or tags.get("name:it") or tags.get("name:de")
            )
            if pass_name:
                return str(pass_name)[:200]

        # Generic fallback
        return f"{category.title()} #{element_id}"

    @staticmethod
    def extract_description(tags: dict[str, Any], category: str) -> str:
        """Extract description from OSM tags."""
        description_parts = []

        # Primary description
        if tags.get("description"):
            desc = str(tags["description"]).strip()
            if desc:
                description_parts.append(desc[:150])

        # Elevation
        elevation = POIDataParser.parse_elevation(tags.get("ele"))
        if elevation is not None:
            description_parts.append(f"Elevation: {elevation:.0f}m")

        # Additional info
        if tags.get("historic"):
            description_parts.append(f"Historic: {tags['historic']}")

        if tags.get("amenity"):
            description_parts.append(f"Amenity: {tags['amenity']}")

        if tags.get("tourism"):
            description_parts.append(f"Tourism: {tags['tourism']}")

        if tags.get("wikidata"):
            description_parts.append(f"Wikidata: {tags['wikidata']}")

        # Join parts
        if description_parts:
            return " | ".join(description_parts)

        # Default description
        return f"{category.replace('_', ' ').title()} point of interest"


class POIDataProcessor:
    """Processor for OSM POI JSON data."""

    def __init__(self, category: str):
        """Initialize data processor."""
        self.category = category
        self.elements: list[dict[str, Any]] = []
        self.valid_pois: list[dict[str, Any]] = []

    def process_osm_data(self, osm_data: dict[str, Any]) -> None:
        """Extract POI elements from OSM data."""
        self.elements = osm_data.get("elements", [])
        logger.info(f"Processing {len(self.elements)} elements for {self.category}")

    def is_element_valid(self, element: dict[str, Any]) -> bool:
        """Check if element is valid for processing."""
        if "id" not in element:
            return False

        # Check if it has coordinates
        coords = POIDataParser.extract_coordinates(element)
        if not coords:
            return False
        return coords

    def create_poi_data(self, element: dict[str, Any]) -> dict[str, Any] | None:
        """Create POI data dictionary from OSM element."""
        if not self.is_element_valid(element):
            return None

        coords = POIDataParser.extract_coordinates(element)
        if not coords:
            return None

        tags = element.get("tags", {})

        name = POIDataParser.extract_name(tags, self.category, element["id"])
        description = POIDataParser.extract_description(tags, self.category)
        elevation = POIDataParser.parse_elevation(tags.get("ele"))

        try:
            point = Point(coords[0], coords[1], srid=4326)
        except Exception as e:
            logger.debug(f"Failed to create Point: {e}")
            return None

        return {
            "osm_id": element["id"],
            "name": name,
            "category": self.category,
            "location": point,
            "description": description[:500],
            "elevation": elevation,
            "tags": tags,
        }

    def process_all_elements(self) -> list[dict[str, Any]]:
        """Process all elements and create POI data."""
        self.valid_pois = []

        for i, element in enumerate(self.elements):
            poi_data = self.create_poi_data(element)
            if poi_data:
                self.valid_pois.append(poi_data)

            # Log progress for large imports
            if i > 0 and i % 1000 == 0:
                logger.info(f"Processed {i:,}/{len(self.elements):,} elements...")

        logger.info(f"Created {len(self.valid_pois)} valid POIs for {self.category}")
        return self.valid_pois


class POIDatabaseManager:
    """Manager for POI database operations."""

    @staticmethod
    def clear_existing_pois() -> int:
        """Clear all existing POIs."""
        count = PointOfInterest.objects.count()
        PointOfInterest.objects.all().delete()
        return count

    @staticmethod
    def clear_category_pois(category: str) -> int:
        """Clear existing POIs for a specific category."""
        count = PointOfInterest.objects.filter(category=category).count()
        PointOfInterest.objects.filter(category=category).delete()
        return count

    @staticmethod
    @transaction.atomic
    def save_pois_batch(pois: list[dict[str, Any]], batch_num: int) -> tuple[bool, int]:
        """Save a batch of POIs to database."""
        if not pois:
            return True, 0

        try:
            poi_objects = []
            for poi_data in pois:
                poi_objects.append(PointOfInterest(**poi_data))

            created = PointOfInterest.objects.bulk_create(
                poi_objects, ignore_conflicts=True, batch_size=len(poi_objects)
            )

            saved_count = len(created)
            logger.info(f"Batch {batch_num}: Saved {saved_count} POIs")
            return True, saved_count

        except Exception as e:
            logger.error(f"Batch {batch_num}: Failed to save POIs - {e}")
            return False, 0

    @staticmethod
    def save_all_pois(pois: list[dict[str, Any]], batch_size: int = 1000) -> int:
        """Save all POIs to database in batches."""
        if not pois:
            logger.warning("No POIs to save")
            return 0

        total_pois = len(pois)
        total_saved = 0

        logger.info(f"Saving {total_pois:,} POIs...")

        for i in range(0, total_pois, batch_size):
            batch = pois[i : i + batch_size]
            batch_num = i // batch_size + 1

            success, saved = POIDatabaseManager.save_pois_batch(batch, batch_num)
            if success:
                total_saved += saved

        logger.info(f"Saved {total_saved:,}/{total_pois:,} POIs")
        return total_saved

    @staticmethod
    def get_database_stats() -> dict[str, Any]:
        """Get database statistics with intelligent formatting."""
        total = PointOfInterest.objects.count()

        # Get category statistics with counts
        category_stats = (
            PointOfInterest.objects.values("category")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        # Format categories intelligently (top 5 only)
        categories_summary = []
        total_shown = 0

        for stat in category_stats[:5]:  # Show only top 5
            percentage = (stat["count"] / total * 100) if total > 0 else 0
            categories_summary.append(
                {
                    "category": stat["category"],
                    "count": stat["count"],
                    "percentage": round(percentage, 1),
                }
            )
            total_shown += stat["count"]

        # Add summary for remaining categories
        remaining_count = total - total_shown
        remaining_categories = len(category_stats) - 5

        if remaining_count > 0 and remaining_categories > 0:
            categories_summary.append(
                {
                    "category": f"Other ({remaining_categories} categories)",
                    "count": remaining_count,
                    "percentage": round((remaining_count / total * 100), 1)
                    if total > 0
                    else 0,
                }
            )

        return {
            "total_pois": total,
            "unique_categories": len(category_stats),
            "categories_summary": categories_summary,
            "category_stats": list(category_stats),  # Full data if needed
        }


class CategoryImporter:
    """Importer for a single POI category."""

    def __init__(self, category: str, area_name: str):
        """Initialize the importer."""
        self.category = category
        self.area_name = area_name
        self.stats = {
            "elements_fetched": 0,
            "pois_created": 0,
            "pois_saved": 0,
        }

    def import_category(self, clear_existing: bool = False) -> dict[str, Any]:
        """Import POIs for a single category."""
        logger.info(f"Importing {self.category} POIs for {self.area_name}")

        try:
            # Clear existing POIs for this category if requested
            if clear_existing:
                deleted = POIDatabaseManager.clear_category_pois(self.category)
                logger.info(f"Cleared {deleted} existing {self.category} POIs")

            # Fetch data from OSM
            api_client = POIAPIClient()
            data = api_client.fetch_pois_for_category(self.category, self.area_name)

            if not data:
                return {
                    "success": False,
                    "error": f"Failed to fetch data for {self.category}",
                    "stats": self.stats,
                }

            self.stats["elements_fetched"] = len(data.get("elements", []))

            if self.stats["elements_fetched"] == 0:
                return {
                    "success": False,
                    "error": f"No {self.category} elements found in {self.area_name}",
                    "stats": self.stats,
                }

            logger.info(
                f"✓ Fetched {self.stats['elements_fetched']:,} {self.category} elements"
            )

            # Process data
            data_processor = POIDataProcessor(self.category)
            data_processor.process_osm_data(data)
            pois = data_processor.process_all_elements()

            self.stats["pois_created"] = len(pois)

            if self.stats["pois_created"] == 0:
                return {
                    "success": False,
                    "error": f"No valid {self.category} POIs could be created",
                    "stats": self.stats,
                }

            logger.info(
                f"✓ Created {self.stats['pois_created']:,} {self.category} POIs"
            )

            # Save to database
            saved_count = POIDatabaseManager.save_all_pois(pois)
            self.stats["pois_saved"] = saved_count

            return {
                "success": True,
                "pois_saved": saved_count,
                "stats": self.stats,
            }

        except Exception as e:
            logger.error(f"Error importing {self.category} POIs: {e}")
            return {
                "success": False,
                "error": str(e),
                "stats": self.stats,
            }


class POIImportPipeline:
    """Main pipeline for importing POIs."""

    def __init__(self):
        """Initialize pipeline."""
        self.stats = {
            "categories_processed": 0,
            "total_elements_fetched": 0,
            "total_pois_created": 0,
            "total_pois_saved": 0,
        }

    def import_categories(
        self, categories: list[str], area_name: str, clear_existing: bool = False
    ) -> dict[str, Any]:
        """Import multiple POI categories."""
        logger.info(f"Starting POI import for area: {area_name}")

        start_time = time.time()
        results = []
        errors = []

        for category in categories:
            category_importer = CategoryImporter(category, area_name)
            result = category_importer.import_category(clear_existing)
            results.append(result)

            # Update overall statistics
            self.stats["categories_processed"] += 1
            self.stats["total_elements_fetched"] += result["stats"]["elements_fetched"]
            self.stats["total_pois_created"] += result["stats"]["pois_created"]
            self.stats["total_pois_saved"] += result["stats"]["pois_saved"]

            if not result["success"]:
                errors.append(f"{category}: {result.get('error', 'Unknown error')}")

        total_time = time.time() - start_time

        # Summary log with clean formatting
        if self.stats["total_pois_saved"] > 0:
            logger.info("=" * 60)
            logger.info("✓ POI IMPORT COMPLETED SUCCESSFULLY")
            logger.info(f"Total time: {total_time:.1f}s")
            logger.info(f"Categories processed: {self.stats['categories_processed']}")
            logger.info(f"Elements fetched: {self.stats['total_elements_fetched']:,}")
            logger.info(f"POIs created: {self.stats['total_pois_created']:,}")
            logger.info(f"POIs saved: {self.stats['total_pois_saved']:,}")

            # Get category breakdown for summary
            category_stats = (
                PointOfInterest.objects.values("category")
                .annotate(count=Count("id"))
                .order_by("-count")[:3]
            )  # Show top 3 in summary

            if category_stats:
                logger.info("🏷️  Top categories:")
                for stat in category_stats:
                    percentage = stat["count"] / self.stats["total_pois_saved"] * 100
                    logger.info(
                        f"   {stat['category']}: {stat['count']:,} ({percentage:.1f}%)"
                    )

            logger.info("=" * 60)

            return {
                "success": True,
                "pois_saved": self.stats["total_pois_saved"],
                "errors": errors,
                "stats": self.stats,
            }
        else:
            logger.error("=" * 60)
            logger.error("✗ POI IMPORT FAILED")
            logger.error("No POIs were saved")
            if errors:
                logger.error("Errors:")
                for error in errors:
                    logger.error(f"  - {error}")
            logger.error("=" * 60)

            return {
                "success": False,
                "pois_saved": 0,
                "errors": errors,
                "stats": self.stats,
            }


class Command(BaseCommand):
    """Import OSM Points of Interest command."""

    help = "Import Points of Interest from OpenStreetMap"

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            "--area",
            type=str,
            default="test",
            help="Area to import (test, italy, lazio, lombardia, etc.)",
        )
        parser.add_argument(
            "--categories",
            type=str,
            default="viewpoint,restaurant,church,historic",
            help="Comma-separated list of categories to import or 'all'",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            default=True,
            help="Clear existing POIs before import (default: True)",
        )
        parser.add_argument(
            "--no-clear",
            action="store_false",
            dest="clear",
            help="Do not clear existing POIs before import",
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
            logging.basicConfig(
                level=logging.DEBUG,
                format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            )
        else:
            logging.basicConfig(
                level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
            )

        area_name = options["area"]

        # Parse categories
        if options["categories"].lower() == "all":
            categories = POIConfig.get_available_categories()
        else:
            categories = []
            for cat in options["categories"].split(","):
                cat = cat.strip().lower()
                if cat in POIConfig.get_available_categories():
                    categories.append(cat)

        if not categories:
            self.stdout.write(self.style.ERROR("No valid categories specified"))
            return

        # Display header
        self.display_header(area_name, categories, options)

        # Run import
        pipeline = POIImportPipeline()
        result = pipeline.import_categories(
            categories=categories, area_name=area_name, clear_existing=options["clear"]
        )

        # Display results
        self.display_results(result)

    def display_header(self, area_name: str, categories: list[str], options):
        """Display command header."""
        self.stdout.write("=" * 60)
        self.stdout.write("OSM POI IMPORT")
        self.stdout.write("=" * 60)
        self.stdout.write(f"Area: {area_name}")
        self.stdout.write(f"Categories: {', '.join(categories)}")
        self.stdout.write(f"Clear existing: {'Yes' if options['clear'] else 'No'}")
        self.stdout.write("=" * 60)

    def display_results(self, result: dict[str, Any]):
        """Display results for import with intelligent formatting."""
        self.stdout.write("\n" + "=" * 60)

        if result.get("success"):
            self.stdout.write(self.style.SUCCESS("✓ IMPORT SUCCESSFUL"))
            stats = result.get("stats", {})

            self.stdout.write("\nStatistics:")
            self.stdout.write(
                f"  Categories processed: {stats.get('categories_processed', 0)}"
            )
            self.stdout.write(
                f"  Elements fetched:     {stats.get('total_elements_fetched', 0):,}"
            )
            self.stdout.write(
                f"  POIs created:         {stats.get('total_pois_created', 0):,}"
            )
            self.stdout.write(
                f"  POIs saved:           {stats.get('total_pois_saved', 0):,}"
            )

            # Get database stats with intelligent formatting
            db_stats = POIDatabaseManager.get_database_stats()
            self.stdout.write("\nDatabase:")
            self.stdout.write(f"  Total POIs:          {db_stats['total_pois']:,}")
            self.stdout.write(f"  Unique categories:   {db_stats['unique_categories']}")

            # Show category breakdown intelligently
            if db_stats["categories_summary"]:
                self.stdout.write("\nCategory breakdown:")
                for cat_info in db_stats["categories_summary"]:
                    if isinstance(cat_info, dict):
                        # New format with dictionary
                        self.stdout.write(
                            f"  {cat_info['category']:25}"
                            f" {cat_info['count']:6,}"
                            f" ({cat_info['percentage']:.1f}%)"
                        )
                    else:
                        # Old format for compatibility
                        self.stdout.write(f"  {cat_info}")

        else:
            self.stdout.write(self.style.ERROR("✗ IMPORT FAILED"))
            if result.get("errors"):
                self.stdout.write("\nErrors:")
                for error in result["errors"]:
                    self.stdout.write(f"  - {error}")

            # Show partial statistics if available
            if result.get("stats", {}).get("total_pois_saved", 0) > 0:
                self.stdout.write("\nPartial results:")
                self.stdout.write(
                    f"  POIs saved: {result['stats']['total_pois_saved']:,}"
                )

        self.stdout.write("=" * 60)

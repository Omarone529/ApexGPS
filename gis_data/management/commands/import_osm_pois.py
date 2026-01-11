import logging
import os
import time
from typing import Any

import requests
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand
from django.db import transaction

from gis_data.models import PointOfInterest

logger = logging.getLogger(__name__)


class QueryConstructor:
    """Constructs Overpass API queries."""

    @staticmethod
    def build_query(category: str, bbox: str) -> str:
        """Build Overpass query for a category."""
        category_filters = {
            "viewpoint": 'node["tourism"="viewpoint"]',
            "restaurant": 'node["amenity"="restaurant"]',
            "church": 'node["amenity"="place_of_worship"]',
            "historic": 'node["historic"]',
        }

        if category not in category_filters:
            raise ValueError(f"Unknown category: {category}")

        query = f"""[out:json][timeout:180];
        (
          {category_filters[category]}({bbox});
        );
        out body;
        """
        return query.strip()


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

    def execute_query(self, endpoint: str, query: str) -> dict | None:
        """Execute Overpass query."""
        try:
            response = self.session.post(endpoint, data={"data": query}, timeout=180)

            if response.status_code == 200:
                return response.json()

        except requests.exceptions.RequestException:
            pass

        return None


class POIDataParser:
    """Parses OSM element data into POI objects."""

    @staticmethod
    def parse_element(element: dict, category: str) -> dict | None:
        """Parse OSM element into POI dictionary."""
        if element.get("type") != "node":
            return None

        try:
            lat = element.get("lat")
            lon = element.get("lon")

            if not lat or not lon:
                return None

            tags = element.get("tags", {})

            name = tags.get("name", "")
            if not name:
                if category == "viewpoint":
                    name = "Punto panoramico"
                elif category == "restaurant":
                    name = tags.get("cuisine", "Ristorante")
                elif category == "church":
                    name = tags.get("denomination", "Chiesa")
                else:
                    name = "Punto di interesse"

            description_parts = []
            if tags.get("description"):
                description_parts.append(tags["description"][:100])
            if tags.get("historic"):
                description_parts.append(f"Storico: {tags['historic']}")

            return {
                "osm_id": element.get("id"),
                "name": str(name)[:200],
                "category": category,
                "location": Point(float(lon), float(lat), srid=4326),
                "description": " | ".join(description_parts)
                if description_parts
                else "",
                "tags": tags,
            }
        except Exception:
            return None


class CategoryImporter:
    """Imports POIs for a single category."""

    def __init__(self, category: str):
        """Initialize importer."""
        self.category = category
        self.api_client = APIClient()
        self.parser = POIDataParser()
        self.endpoints = self._get_endpoints_from_env()

    def _get_endpoints_from_env(self) -> list[str]:
        """Get endpoints from environment variables."""
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
                "https://overpass.osm.ch/api/interpreter",
            ]

        return endpoints

    def import_category(self, bbox: str) -> dict[str, Any]:
        """Import POIs for this category."""
        query = QueryConstructor.build_query(self.category, bbox)

        for endpoint in self.endpoints:
            data = self.api_client.execute_query(endpoint, query)
            if not data:
                time.sleep(2)
                continue

            elements = data.get("elements", [])
            if not elements:
                continue

            pois = []
            for element in elements:
                poi = self.parser.parse_element(element, self.category)
                if poi:
                    pois.append(poi)

            if not pois:
                continue

            saved = self._save_pois(pois)

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

    def _save_pois(self, pois: list[dict]) -> int:
        """Save POIs to database."""
        if not pois:
            return 0

        poi_objects = []
        for poi_data in pois:
            poi_objects.append(PointOfInterest(**poi_data))

        try:
            with transaction.atomic():
                saved = PointOfInterest.objects.bulk_create(
                    poi_objects, ignore_conflicts=True, batch_size=500
                )
            return len(saved)
        except Exception:
            saved = 0
            for poi_data in pois:
                try:
                    PointOfInterest.objects.create(**poi_data)
                    saved += 1
                except Exception:
                    pass

            return saved


class Command(BaseCommand):
    """Django management command for OSM POI import."""

    help = "Import Points of Interest from OpenStreetMap"

    def add_arguments(self, parser):
        """Add arguments to parser."""
        parser.add_argument(
            "--area",
            default="italy",
            help="Area to import (italy, test, or bbox)",
        )
        parser.add_argument(
            "--categories",
            default="viewpoint,restaurant,church",
            help="Comma-separated categories to import",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Enable verbose output",
        )

    def handle(self, *args, **options):
        """Execute the command."""
        if options["verbose"]:
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.INFO)

        categories = [
            cat.strip() for cat in options["categories"].split(",") if cat.strip()
        ]

        if not categories:
            self.stdout.write(self.style.ERROR("No categories specified"))
            return

        bbox = self._get_bbox(options["area"])

        results = []
        for category in categories:
            importer = CategoryImporter(category)
            result = importer.import_category(bbox)
            results.append(result)

            if category != categories[-1]:
                time.sleep(2)

        self._display_results(results)

    def _get_bbox(self, area: str) -> str:
        """Get bounding box for area."""
        area = area.lower()

        if area == "test":
            return "41.88,12.47,41.90,12.49"
        elif area == "umbria":
            return "42.5,12.0,43.5,13.5"
        else:
            return "35.5,6.6,47.1,18.5"

    def _display_results(self, results: list[dict[str, Any]]):
        """Display import results."""
        self.stdout.write("\n" + "=" * 60)

        successful = [r for r in results if r.get("success")]
        failed = [r for r in results if not r.get("success")]

        if successful:
            self.stdout.write(self.style.SUCCESS("POI IMPORT RESULTS"))
            self.stdout.write("-" * 60)

            for result in successful:
                self.stdout.write(
                    f"{result['category']}: " f"{result['pois_saved']:,} POIs saved"
                )

        if failed:
            self.stdout.write("\n" + self.style.WARNING("FAILED IMPORTS:"))
            for result in failed:
                self.stdout.write(f"✗ {result['category']}")

        total_pois = PointOfInterest.objects.count()
        self.stdout.write("\n" + "-" * 60)
        self.stdout.write(f"Total POIs in database: {total_pois:,}")
        self.stdout.write("=" * 60)

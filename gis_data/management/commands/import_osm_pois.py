import os
import time

import requests
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.utils import IntegrityError

from gis_data.models import PointOfInterest


class OSMImporter:
    """
    OpenStreetMap data importer for Points of Interest.

    Handles Overpass API queries and data extraction for various
    POI categories relevant to scenic motorcycle routing.
    """

    OSM_URL = os.environ.get("OSM_URL")

    QUERIES = {
        "panoramic": """
            [out:json][timeout:300];
            area["name"="{area}"]->.searchArea;
            (
                node["tourism"="viewpoint"](area.searchArea);
                node["natural"="peak"](area.searchArea);
                way["tourism"="viewpoint"](area.searchArea);
                relation["tourism"="viewpoint"](area.searchArea);
            );
            out body;
            >;
            out skel qt;
        """,
        "mountain_pass": """
            [out:json][timeout:300];
            area["name"="{area}"]->.searchArea;
            (
                node["mountain_pass"="yes"](area.searchArea);
                node["natural"="saddle"](area.searchArea);
                node["name"~"Passo|Colle|Pass"](area.searchArea);
            );
            out body;
            >;
            out skel qt;
        """,
        "twisty_road": """
            [out:json][timeout:300];
            area["name"="{area}"]->.searchArea;
            (
                way["name"~"Strada delle|Strada dei|Via Curva|Twisty"](area.searchArea);
                way["description"~"curva|twisty|winding"](area.searchArea);
            );
            out body;
            >;
            out skel qt;
        """,
        "viewpoint": """
            [out:json][timeout:300];
            area["name"="{area}"]->.searchArea;
            (
                node["tourism"="viewpoint"](area.searchArea);
                node["amenity"="viewpoint"](area.searchArea);
            );
            out body;
            >;
            out skel qt;
        """,
        "lake": """
            [out:json][timeout:300];
            area["name"="{area}"]->.searchArea;
            (
                node["natural"="water"]["water"="lake"](area.searchArea);
                way["natural"="water"]["water"="lake"](area.searchArea);
                relation["natural"="water"]["water"="lake"](area.searchArea);
            );
            out center;
            out body;
            >;
            out skel qt;
        """,
        "monument": """
            [out:json][timeout:300];
            area["name"="{area}"]->.searchArea;
            (
                node["historic"](area.searchArea);
                way["historic"](area.searchArea);
                relation["historic"](area.searchArea);
            );
            out body;
            >;
            out skel qt;
        """,
        "biker_meeting": """
            [out:json][timeout:300];
            area["name"="{area}"]->.searchArea;
            (
                node["amenity"="restaurant"]["description"~"moto|biker"](area.searchArea);
                node["tourism"="attraction"]["name"~"Moto|Biker"](area.searchArea);
            );
            out body;
            >;
            out skel qt;
        """,
    }

    @classmethod
    def _execute_overpass_query(cls, query):
        """Execute query against Overpass API."""
        try:
            response = requests.post(cls.OSM_URL, data={"data": query}, timeout=300)
            response.raise_for_status()
            return response.json().get("elements", [])
        except requests.RequestException:
            return []

    @classmethod
    def fetch_pois(cls, area: str = "Italy", category: str = None):
        """
        Fetch POIs from OpenStreetMap for a specific area and category.

        Args:
            area: Geographic area name (e.g., "Italy", "Lombardia")
            category: POI category to fetch from available QUERIES

        Returns:
            list[dict]: Raw OSM elements for the specified category

        Raises:
            ValueError: If the specified category is not supported
        """
        if category not in cls.QUERIES:
            raise ValueError(
                f"Unknown category: {category}. Available: {list(cls.QUERIES.keys())}"
            )

        query = cls.QUERIES[category].format(area=area)
        return cls._execute_overpass_query(query)

    @classmethod
    def _extract_coordinates(cls, element):
        """Extract coordinates from OSM element."""
        if element.get("type") == "node":
            lat = element.get("lat")
            lon = element.get("lon")
        elif element.get("center"):
            lat = element.get("center", {}).get("lat")
            lon = element.get("center", {}).get("lon")
        else:
            return None, None

        return lat, lon

    @classmethod
    def _extract_name(cls, tags, category, element_id):
        """Extract name from OSM tags."""
        return tags.get("name") or tags.get("description") or f"{category}_{element_id}"

    @classmethod
    def _extract_description(cls, tags):
        """Extract description from OSM tags."""
        description_parts = []
        if tags.get("description"):
            description_parts.append(tags["description"])
        if tags.get("ele"):
            description_parts.append(f"Elevation: {tags['ele']}m")
        if tags.get("wikidata"):
            description_parts.append(f"Wikidata: {tags['wikidata']}")

        return " | ".join(description_parts) if description_parts else None

    @classmethod
    def extract_poi_info(cls, element: dict, category: str):
        """
        Extract relevant information from OSM element for database storage.

        Args:
            element: Raw OSM API element (node, way, or relation)
            category: POI category for classification

        Returns:
            dict: Structured POI information including name, coordinates,
                  description, and OSM metadata, or None if invalid
        """
        lat, lon = cls._extract_coordinates(element)
        if not lat or not lon:
            return None

        tags = element.get("tags", {})
        name = cls._extract_name(tags, category, element["id"])
        description = cls._extract_description(tags)

        return {
            "name": name[:255],
            "category": category,
            "location": Point(float(lon), float(lat), srid=4326),
            "description": description,
            "osm_id": element.get("id"),
            "osm_tags": tags,
        }

    @classmethod
    def _find_existing_poi(cls, location):
        """Find existing POI by location proximity."""
        return PointOfInterest.objects.filter(
            location__dwithin=(location, 0.001)
        ).first()

    @classmethod
    def _update_existing_poi(cls, existing, poi_info):
        """Update existing POI with new information."""
        existing.name = poi_info["name"]
        existing.category = poi_info["category"]
        existing.description = poi_info["description"]
        existing.save()

    @classmethod
    def _create_new_poi(cls, poi_info):
        """Create new POI record."""
        PointOfInterest.objects.create(**poi_info)

    @classmethod
    def _process_osm_element(cls, element, category):
        """Process single OSM element into POI."""
        poi_info = cls.extract_poi_info(element, category)
        if not poi_info:
            return False, True  # Failed, skipped

        try:
            existing = cls._find_existing_poi(poi_info["location"])
            if existing:
                cls._update_existing_poi(existing, poi_info)
            else:
                cls._create_new_poi(poi_info)
            return True, False  # Success, not skipped
        except IntegrityError:
            return False, True  # Failed, skipped


class Command(BaseCommand):
    """
    Management command for importing Points of Interest from OpenStreetMap.

    This command fetches motorcycle-relevant POIs from OSM within specified
    geographic areas and categories, storing them in the database for use
    in scenic route calculation and POI density scoring.
    """

    help = "Import Points of Interest from OpenStreetMap"

    def _parse_categories_argument(self, categories_arg):
        """Parse categories command-line argument."""
        if categories_arg == "all":
            return list(OSMImporter.QUERIES.keys())
        return [c.strip() for c in categories_arg.split(",")]

    def _clear_existing_pois(self):
        """Clear all existing POIs if requested."""
        PointOfInterest.objects.all().delete()

    def _process_category(self, importer, area, category, limit):
        """Process single category of POIs."""
        if category not in OSMImporter.QUERIES:
            return 0, 0

        elements = importer.fetch_pois(area=area, category=category)
        if not elements:
            return 0, 0

        imported_count = 0
        skipped_count = 0

        for element in elements[: limit or None]:
            success, skipped = OSMImporter._process_osm_element(element, category)
            if success:
                imported_count += 1
            elif skipped:
                skipped_count += 1

        time.sleep(1)  # Rate limiting
        return imported_count, skipped_count

    def add_arguments(self, parser):
        """
        Define command-line arguments for POI import.

        Args:
            parser: argparse.ArgumentParser instance for argument definition
        """
        parser.add_argument(
            "--area",
            type=str,
            default="Italy",
            help="Geographic area to search (e.g., 'Italy', 'Lombardia')",
        )
        parser.add_argument(
            "--categories",
            type=str,
            default="all",
            help="Comma-separated list of categories to import (or 'all')",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear existing POIs before import",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Limit number of POIs per category (0 for no limit)",
        )

    def handle(self, *args, **options):
        """
        Execute the OSM POI import process.

        Orchestrates the complete import workflow:
        1. Determine categories to import
        2. Clear existing data if requested
        3. Fetch and process POIs from OSM for each category
        4. Store imported POIs in the database
        5. Trigger scenic score recalculation

        Args:
            *args: Additional positional arguments
            **options: Command-line options
        """
        area = options["area"]
        categories = self._parse_categories_argument(options["categories"])

        if options["clear"]:
            self._clear_existing_pois()

        importer = OSMImporter()
        total_imported = 0

        with transaction.atomic():
            for category in categories:
                imported_count, _ = self._process_category(
                    importer, area, category, options["limit"]
                )
                total_imported += imported_count

        from django.core.management import call_command

        call_command("prepare_gis_data", "--area", area)

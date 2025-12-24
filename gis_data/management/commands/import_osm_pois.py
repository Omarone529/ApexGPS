"""
Import Points of Interest from OpenStreetMap.
Fetches scenic and motorcycle-relevant POIs within a geographic area.
"""

import logging
import os
import time

import requests
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.utils import IntegrityError

from gis_data.models import PointOfInterest

logger = logging.getLogger(__name__)


class OSMImporter:
    """Handles OpenStreetMap data import for Points of Interest."""

    # API endpoint
    OSM_URL = os.environ.get("OSM_URL")

    # Query templates for different POI categories
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
    def fetch_pois(cls, area: str = "Italy", category: str = None) -> list[dict]:
        """Fetch POIs from OpenStreetMap for a specific area and category."""
        if category not in cls.QUERIES:
            raise ValueError(
                f"Unknown category: {category}. Available: {list(cls.QUERIES.keys())}"
            )

        query = cls.QUERIES[category].format(area=area)

        try:
            response = requests.post(cls.OSM_URL, data={"data": query}, timeout=300)
            response.raise_for_status()
            return response.json().get("elements", [])
        except requests.RequestException as e:
            logger.error(f"Failed to fetch OSM data for {category}: {e}")
            return []

    @classmethod
    def extract_poi_info(cls, element: dict, category: str) -> dict | None:
        """Extract relevant information from OSM element."""
        # Get coordinates
        if element.get("type") == "node":
            lat = element.get("lat")
            lon = element.get("lon")
        elif element.get("center"):
            lat = element.get("center", {}).get("lat")
            lon = element.get("center", {}).get("lon")
        else:
            return None

        if not lat or not lon:
            return None

        # Get name
        tags = element.get("tags", {})
        name = (
            tags.get("name") or tags.get("description") or f"{category}_{element['id']}"
        )

        # Get description
        description_parts = []
        if tags.get("description"):
            description_parts.append(tags["description"])
        if tags.get("ele"):
            description_parts.append(f"Elevation: {tags['ele']}m")
        if tags.get("wikidata"):
            description_parts.append(f"Wikidata: {tags['wikidata']}")

        description = " | ".join(description_parts) if description_parts else None

        return {
            "name": name[:255],  # Truncate to fit CharField
            "category": category,
            "location": Point(float(lon), float(lat), srid=4326),
            "description": description,
            "osm_id": element.get("id"),
            "osm_tags": tags,
        }


class Command(BaseCommand):
    """
    Import Points of Interest from OpenStreetMap.

    This class fetches motorcycle-relevant POIs from OSM and stores them
    in the db for scenic routing calculations.
    """

    help = "Import Points of Interest from OpenStreetMap"

    def add_arguments(self, parser):
        """Define command-line arguments."""
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
        """Execute OSM POI import."""
        area = options["area"]

        # Determine categories to import
        if options["categories"] == "all":
            categories = list(OSMImporter.QUERIES.keys())
        else:
            categories = [c.strip() for c in options["categories"].split(",")]

        # Clear existing data if requested
        if options["clear"]:
            deleted_count, _ = PointOfInterest.objects.all().delete()
            self.stdout.write(f"Cleared {deleted_count} existing POIs")

        importer = OSMImporter()
        total_imported = 0

        with transaction.atomic():
            for category in categories:
                if category not in OSMImporter.QUERIES:
                    self.stdout.write(
                        self.style.WARNING(f"Unknown category: {category}. Skipping.")
                    )
                    continue

                self.stdout.write(f"Fetching {category} POIs in {area}...")

                # Fetch POIs from OSM
                elements = importer.fetch_pois(area=area, category=category)

                if not elements:
                    self.stdout.write(f"  No {category} POIs found")
                    continue

                # Process and save POIs
                imported_count = 0
                skipped_count = 0

                for element in elements[: options["limit"] or None]:
                    poi_info = importer.extract_poi_info(element, category)

                    if not poi_info:
                        skipped_count += 1
                        continue

                    try:
                        # Check if POI already exists (by coordinates or OSM ID)
                        existing = PointOfInterest.objects.filter(
                            location__dwithin=(poi_info["location"], 0.001)  # ~100m
                        ).first()

                        if existing:
                            # Update existing POI
                            existing.name = poi_info["name"]
                            existing.category = poi_info["category"]
                            existing.description = poi_info["description"]
                            existing.save()
                        else:
                            # Create new POI
                            PointOfInterest.objects.create(**poi_info)

                        imported_count += 1

                    except IntegrityError as e:
                        logger.warning(f"Failed to save POI {poi_info['name']}: {e}")
                        skipped_count += 1

                # Rate limiting to be nice to OSM API
                time.sleep(1)

                self.stdout.write(
                    self.style.SUCCESS(
                        f"  Imported {imported_count} {category} POIs "
                        f"(skipped {skipped_count})"
                    )
                )
                total_imported += imported_count

        self.stdout.write(
            self.style.SUCCESS(
                f"\nTotal imported: {total_imported}"
                f" POIs from {len(categories)} categories"
            )
        )

        # Run scenic score calculation to update POI densities
        self.stdout.write("\nUpdating scenic scores with new POIs...")
        from django.core.management import call_command

        call_command("prepare_gis_data", "--area", area)

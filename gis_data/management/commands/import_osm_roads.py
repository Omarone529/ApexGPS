import os

import requests
from django.contrib.gis.geos import LineString
from django.core.management.base import BaseCommand
from django.db import transaction

from gis_data.models import RoadSegment

__all__ = ["Command"]


class OSMRoadImporter:
    """Import OSM roads."""

    OSM_URL = os.environ.get("OSM_URL")

    HIGHWAY_TYPES = [
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "tertiary",
        "unclassified",
        "residential",
        "service",
    ]

    @classmethod
    def get_italy_query(cls):
        """Get query for Italy."""
        highway_filter = "|".join(cls.HIGHWAY_TYPES)
        return f"""
            [out:json][timeout:2500];
            area["ISO3166-1"="IT"]->.it;
            (
                way["highway"]["highway"~"^{highway_filter}$"](area.it);
            );
            out body;
            >;
            out skel qt;
        """

    @classmethod
    def get_region_query(cls, region_name):
        """Get query for region."""
        highway_filter = "|".join(cls.HIGHWAY_TYPES)
        return f"""
            [out:json][timeout:900];
            area["name"="{region_name}"]["boundary"="administrative"]["admin_level"=4]->.reg;
            (
                way["highway"]["highway"~"^{highway_filter}$"](area.reg);
            );
            out body;
            >;
            out skel qt;
        """

    @classmethod
    def fetch_roads(cls, area="italy"):
        """Fetch roads from OSM."""
        if area.lower() == "italy":
            query = cls.get_italy_query()
        else:
            query = cls.get_region_query(area)

        try:
            response = requests.post(cls.OSM_URL, data={"data": query}, timeout=3000)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            return {"elements": []}

    @classmethod
    def process_osm_data(cls, osm_data):
        """Process OSM JSON."""
        nodes = {}
        ways = []

        for element in osm_data.get("elements", []):
            if element["type"] == "node":
                nodes[element["id"]] = (element["lon"], element["lat"])
            elif element["type"] == "way":
                ways.append(element)

        return nodes, ways

    @classmethod
    def create_road_segments(cls, nodes, ways, batch_size=1000):
        """Create RoadSegment objects."""
        segments = []

        for way in ways:
            if "nodes" not in way or len(way["nodes"]) < 2:
                continue

            tags = way.get("tags", {})
            highway_type = tags.get("highway", "unclassified")

            coords = []
            for node_id in way["nodes"]:
                if node_id in nodes:
                    coords.append(nodes[node_id])

            if len(coords) < 2:
                continue

            try:
                line = LineString(coords, srid=4326)
            except (ValueError, TypeError):
                continue

            segment = RoadSegment(
                osm_id=way["id"],
                name=tags.get("name"),
                highway=highway_type,
                geometry=line,
                maxspeed=cls._parse_maxspeed(tags.get("maxspeed")),
                oneway=cls._parse_oneway(tags.get("oneway")),
                surface=tags.get("surface"),
                lanes=cls._parse_lanes(tags.get("lanes")),
            )
            segments.append(segment)

            if len(segments) >= batch_size:
                RoadSegment.objects.bulk_create(segments)
                segments = []

        if segments:
            RoadSegment.objects.bulk_create(segments)

    @classmethod
    def _parse_maxspeed(cls, maxspeed_str):
        """Parse maxspeed."""
        if not maxspeed_str:
            return None

        try:
            if "km/h" in maxspeed_str:
                return int(maxspeed_str.replace("km/h", "").strip())
            return int(maxspeed_str)
        except (ValueError, TypeError):
            return None

    @classmethod
    def _parse_oneway(cls, oneway_str):
        """Parse oneway."""
        if not oneway_str:
            return False

        return oneway_str in ["yes", "true", "1", "-1"]

    @classmethod
    def _parse_lanes(cls, lanes_str):
        """Parse lanes."""
        if not lanes_str:
            return None

        try:
            return int(lanes_str)
        except (ValueError, TypeError):
            return None


class Command(BaseCommand):
    """Import OSM roads command."""

    help = "Import road network from OpenStreetMap"

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            "--area",
            type=str,
            default="italy",
            help="Area to import: 'italy' or region name like 'Lombardia'",
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

    def handle(self, *args, **options):
        """Handle command execution."""
        area = options["area"]
        clear = options["clear"]
        batch_size = options["batch_size"]

        if clear:
            RoadSegment.objects.all().delete()

        importer = OSMRoadImporter()
        osm_data = importer.fetch_roads(area)

        if not osm_data.get("elements"):
            return

        nodes, ways = importer.process_osm_data(osm_data)

        if not ways:
            return

        with transaction.atomic():
            importer.create_road_segments(nodes, ways, batch_size)

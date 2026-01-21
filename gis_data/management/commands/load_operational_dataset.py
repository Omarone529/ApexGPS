import gc
import logging
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from gis_data.models import RoadSegment
from gis_data.utils.osm_utils import (
    OSMAPIClient,
    OSMConfig,
    OSMQueryBuilder,
    RoadDataProcessor,
)

logger = logging.getLogger(__name__)


class RegionalRoadImporter:
    """Import OSM data for regions."""

    def __init__(self, batch_size=1000):
        """Initialize importer."""
        self.api_client = OSMAPIClient()
        self.total_segments_imported = 0
        self.batch_size = batch_size

    def import_region(
        self, region_name: str, clear_existing: bool = False
    ) -> dict[str, Any]:
        """Import roads for a specific region."""
        logger.info(f"Importing roads for: {region_name}")

        bbox = OSMConfig.REGION_BBOXES.get(region_name.lower(), OSMConfig.ITALY_BBOX)
        if region_name.lower() == "test":
            query = OSMQueryBuilder.build_simple_test_query(bbox)
        else:
            query = OSMQueryBuilder.build_road_query(bbox)

        data = self.api_client.execute_query(query)

        if not data:
            return {
                "success": False,
                "region": region_name,
                "error": "Failed to fetch data from OSM",
            }

        elements = data.get("elements", [])
        logger.info(f"Got {len(elements)} elements")

        if not elements:
            return {
                "success": False,
                "region": region_name,
                "error": "No elements found",
            }

        way_count = sum(1 for e in elements if e.get("type") == "way")
        node_count = sum(1 for e in elements if e.get("type") == "node")
        logger.info(f"Elements: {way_count} ways, {node_count} nodes")

        segments = []
        ways_processed = 0
        ways_with_geometry = 0
        for i, element in enumerate(elements):
            if element.get("type") == "way":
                ways_processed += 1

                if "geometry" in element and len(element["geometry"]) >= 2:
                    ways_with_geometry += 1
                    segment = RoadDataProcessor.create_road_segment(element)
                    if segment:
                        segment.region = region_name
                        segments.append(segment)

                # Save batch when full
                if len(segments) >= self.batch_size:
                    saved = self._save_segments_batch(
                        segments, region_name, clear_existing and i == 0
                    )
                    self.total_segments_imported += saved
                    segments = []

                    # Force garbage collection
                    gc.collect()

                if ways_processed % 1000 == 0:
                    logger.info(
                        f"Processed {ways_processed} ways, created"
                        f" {self.total_segments_imported} segments"
                    )

        # Save remaining segments
        if segments:
            saved = self._save_segments_batch(
                segments, region_name, clear_existing and ways_processed == 0
            )
            self.total_segments_imported += saved

        logger.info(
            f"Total: processed {ways_processed} ways, "
            f"{ways_with_geometry} with geometry, "
            f"saved {self.total_segments_imported} segments"
        )

        if self.total_segments_imported == 0:
            return {
                "success": False,
                "region": region_name,
                "error": "No valid road segments created",
            }

        return {
            "success": True,
            "region": region_name,
            "elements_found": len(elements),
            "ways_processed": ways_processed,
            "ways_with_geometry": ways_with_geometry,
            "segments_saved": self.total_segments_imported,
        }

    def _save_segments_batch(
        self, segments: list, region_name: str, clear_existing: bool
    ) -> int:
        """Save a batch of road segments."""
        if clear_existing:
            deleted = RoadSegment.objects.filter(highway__isnull=False).delete()[0]
            logger.info(f"Cleared {deleted} existing road segments")

        if not segments:
            return 0

        try:
            with transaction.atomic():
                created_segments = RoadSegment.objects.bulk_create(
                    segments, ignore_conflicts=True, batch_size=1000
                )
            saved_count = len(created_segments)
            logger.info(f"Saved batch of {saved_count} segments for {region_name}")
            return saved_count

        except Exception as e:
            logger.error(f"Failed to save batch: {e}")

            saved = 0
            for segment in segments:
                try:
                    segment.save()
                    saved += 1
                except Exception:
                    pass

            logger.info(f"Saved {saved} segments individually")
            return saved

    def import_test_only(self) -> dict[str, Any]:
        """Import test data only."""
        return self.import_region("test", clear_existing=True)


class Command(BaseCommand):
    """Django command to import OSM roads."""

    help = (
        "Import road network from OpenStreetMap for the next available Italian region"
    )

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            "--force-region",
            type=str,
            help="Force import of a specific region",
        )
        parser.add_argument(
            "--clear", action="store_true", help="Clear existing roads before import"
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show detailed output",
        )
        parser.add_argument(
            "--skip-pois",
            action="store_true",
            help="Skip POI import",
        )
        parser.add_argument(
            "--skip-gis",
            action="store_true",
            help="Skip GIS preparation",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Batch size for processing (default: 1000)",
        )

    def get_next_available_region(self):
        """Get the first available region that needs importing."""
        # Get all Italian regions from OSMConfig
        all_regions = OSMConfig.ALL_REGIONS

        # Check each region to see if it has enough data
        for region in all_regions:
            # Count existing road segments for this region
            existing_count = RoadSegment.objects.filter(region=region).count()

            # If less than threshold, this region needs importing
            if existing_count < 100:  # Threshold for "enough data"
                logger.info(
                    f"Region {region} has only {existing_count} segments, needs import"
                )
                return region

        # If all regions have enough data
        logger.info("All regions appear to have sufficient data")
        return None

    def handle(self, *args, **options):
        """Execute command - MODIFICATO per importare solo una regione."""
        if options["verbose"]:
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.INFO)

        importer = RegionalRoadImporter(batch_size=options["batch_size"])

        # Determine which region to import
        if options["force_region"]:
            region_to_import = options["force_region"]
            self.stdout.write(f"Forcing import of region: {region_to_import}")
        else:
            region_to_import = self.get_next_available_region()

            if not region_to_import:
                self.stdout.write("All Italian regions appear to be already imported.")
                self.stdout.write(
                    f"Total road segments in database: {RoadSegment.objects.count():,}"
                )
                return

        self.stdout.write(f"Importing region: {region_to_import}")

        # Import roads for this region
        result = importer.import_region(region_to_import, clear_first=options["clear"])

        if not result["success"]:
            self.stderr.write(
                f"Failed to import region {region_to_import}:"
                f" {result.get('error', 'Unknown error')}"
            )
            return

        self.stdout.write(
            f"Successfully imported {result['segments_saved']:,}"
            f" road segments for {region_to_import}"
        )

        # Import POIs for this region (unless skipped)
        if not options["skip_pois"]:
            self.stdout.write(f"Importing POIs for {region_to_import}...")
            from django.core.management import call_command

            try:
                call_command(
                    "import_osm_pois",
                    area=region_to_import,
                    categories="viewpoint,restaurant,church,historic",
                    verbose=options["verbose"],
                )
                self.stdout.write(f"POIs imported for {region_to_import}")
            except Exception as e:
                self.stderr.write(f"Failed to import POIs: {e}")

        # Prepare GIS data (unless skipped)
        if not options["skip_gis"]:
            self.stdout.write("Preparing GIS data for routing...")
            from django.core.management import call_command

            try:
                call_command(
                    "prepare_gis_data",
                    area="italy",  # Use 'italy' to prepare all data
                    force=True,
                    verbose=options["verbose"],
                )
                self.stdout.write("GIS data prepared for routing")
            except Exception as e:
                self.stderr.write(f"Failed to prepare GIS data: {e}")

        # Display final summary
        self._display_results(result)

    def _display_results(self, result: dict[str, Any]):
        """Display import results."""
        total_roads = RoadSegment.objects.count()

        self.stdout.write("\n" + "=" * 60)
        if result.get("success"):
            self.stdout.write("IMPORT SUCCESSFUL")
            self.stdout.write(f"Region: {result['region']}")
            self.stdout.write(f"Segments saved: {result['segments_saved']:,}")
        else:
            self.stdout.write("IMPORT FAILED")
            if "error" in result:
                self.stdout.write(f"Error: {result['error']}")

        self.stdout.write(f"Total road segments in database: {total_roads:,}")

        # Check if topology exists
        from django.db import connection

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_name = 'gis_data_roadsegment_vertices_pgr'
                    )
                """
                )
                has_topology = cursor.fetchone()[0]
                self.stdout.write(
                    f"Routing topology ready: "
                    f"{'Ready' if has_topology else 'Not ready'}"
                )
        except Exception:
            self.stdout.write("Routing topology:(error checking)")

        self.stdout.write("=" * 60)

import gc
import logging
import time
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

            # Fallback: save individually
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

    help = "Import road network from OpenStreetMap for Italian regions"

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            "--regions",
            type=str,
            default="all",
            help="Comma-separated regions to import or 'all' for all regions",
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
            "--test-only",
            action="store_true",
            help="Import only minimal test data",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Batch size for processing (default: 1000)",
        )

    def handle(self, *args, **options):
        """Execute command."""
        if options["verbose"]:
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.INFO)

        importer = RegionalRoadImporter(batch_size=options["batch_size"])

        if options["test_only"]:
            result = importer.import_test_only()
        else:
            regions_arg = options["regions"].strip()

            if regions_arg.lower() == "all":
                result = self._import_all_regions(importer, options["clear"])
            else:
                regions = [r.strip() for r in regions_arg.split(",") if r.strip()]
                result = self._import_specific_regions(
                    importer, regions, options["clear"]
                )

        self._display_results(result)

    def _import_all_regions(self, importer, clear_first):
        """Import all Italian regions."""
        successful_regions = []
        failed_regions = []

        if clear_first:
            RoadSegment.objects.all().delete()

        for region in OSMConfig.ALL_REGIONS:
            result = importer.import_region(region, clear_existing=False)

            if result["success"]:
                successful_regions.append(region)
            else:
                failed_regions.append(region)

            if region != OSMConfig.ALL_REGIONS[-1]:
                time.sleep(30)

        return {
            "success": len(successful_regions) > 0,
            "successful_regions": successful_regions,
            "failed_regions": failed_regions,
            "total_segments": importer.total_segments_imported,
        }

    def _import_specific_regions(self, importer, regions, clear_first):
        """Import specific regions."""
        successful_regions = []
        failed_regions = []
        total_segments = 0

        for i, region in enumerate(regions):
            if clear_first and i == 0:
                RoadSegment.objects.all().delete()

            result = importer.import_region(region, clear_existing=False)

            if result["success"]:
                successful_regions.append(region)
                total_segments += result.get("segments_saved", 0)
            else:
                failed_regions.append(region)

        return {
            "success": len(successful_regions) > 0,
            "successful_regions": successful_regions,
            "failed_regions": failed_regions,
            "total_segments": total_segments,
        }

    def _display_results(self, result: dict[str, Any]):
        """Display import results."""
        if result.get("success"):
            self.stdout.write("Import successful")

            if "successful_regions" in result:
                self.stdout.write(
                    f"Regions imported: {len(result['successful_regions'])}"
                )

            if "total_segments" in result:
                self.stdout.write(f"Segments saved: {result['total_segments']:,}")
        else:
            self.stdout.write("Import failed")
            if "error" in result:
                self.stdout.write(f"Error: {result['error']}")

        total_roads = RoadSegment.objects.count()
        self.stdout.write(f"Total road segments in database: {total_roads:,}")

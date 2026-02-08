import gc
import logging
import time
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Avg

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
        logger.info(f"🚗 Importing roads for: {region_name}")

        bbox = OSMConfig.REGION_BBOXES.get(region_name.lower(), OSMConfig.ITALY_BBOX)

        # Usa query semplice per test e regioni piccole
        if region_name.lower() == "test":
            logger.info("Using simple test query")
            query = OSMQueryBuilder.build_simple_test_query(bbox)
        else:
            query = OSMQueryBuilder.build_road_query(bbox)

        data = self.api_client.execute_query(query)

        if not data:
            logger.error(f"Failed to fetch data from OSM for {region_name}")
            return {
                "success": False,
                "region": region_name,
                "error": "Failed to fetch data from OSM",
            }

        elements = data.get("elements", [])
        logger.info(f"Got {len(elements)} elements from OSM")

        if not elements:
            logger.warning(f"No elements found for {region_name}")
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

        # Limita per test
        max_ways = 5000 if region_name.lower() == "test" else float('inf')

        for i, element in enumerate(elements):
            if element.get("type") == "way":
                ways_processed += 1

                if ways_processed > max_ways:
                    logger.info(f"Reached maximum of {max_ways} ways for {region_name}")
                    break

                if "geometry" in element and len(element["geometry"]) >= 2:
                    ways_with_geometry += 1
                    segment = RoadDataProcessor.create_road_segment(element, region_name)
                    if segment:
                        segments.append(segment)
                        if len(segments) % 100 == 0:
                            logger.info(f"Created {len(segments)} segments...")

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
            logger.error(f"No valid road segments created for {region_name}")
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
            try:
                deleted = RoadSegment.objects.filter(region=region_name).delete()[0]
                logger.info(f"Cleared {deleted} existing road segments for {region_name}")
            except Exception as e:
                logger.error(f"Error clearing segments: {e}")

        if not segments:
            return 0

        try:
            with transaction.atomic():
                created_segments = RoadSegment.objects.bulk_create(
                    segments, ignore_conflicts=True, batch_size=500
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
        logger.info("Starting test import only")
        return self.import_region("test", clear_existing=True)


class Command(BaseCommand):
    """Django command to import OSM roads."""

    help = "Import road network from OpenStreetMap for Italian regions"

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            "--regions",
            type=str,
            default="test",
            help="Comma-separated regions to import or 'test' for test area",
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
            regions_arg = options["regions"].strip().lower()

            if regions_arg == "test":
                result = importer.import_region("test", options["clear"])
            elif regions_arg in ["italy", "all"]:
                result = self._import_all_regions(importer, options["clear"])
            else:
                regions = [r.strip() for r in regions_arg.split(",") if r.strip()]
                result = self._import_specific_regions(
                    importer, regions, options["clear"]
                )

        self._display_results(result)

    def _import_all_regions(self, importer, clear_first):
        """Import all Italian regions."""
        logger.info("🇮🇹 Starting import of all Italian regions")

        successful_regions = []
        failed_regions = []

        if clear_first:
            try:
                RoadSegment.objects.all().delete()
                logger.info("Cleared all existing road segments")
            except Exception as e:
                logger.error(f"Error clearing segments: {e}")

        # Import region per region con pausa
        for i, region in enumerate(OSMConfig.ALL_REGIONS):
            logger.info(f"\n{'=' * 60}")
            logger.info(f"Importing region {i + 1}/{len(OSMConfig.ALL_REGIONS)}: {region}")

            result = importer.import_region(region, clear_existing=False)

            if result["success"]:
                successful_regions.append(region)
                logger.info(f"{region}: {result['segments_saved']} segments imported")
            else:
                failed_regions.append(region)
                logger.error(f" {region} failed: {result.get('error', 'Unknown error')}")

            if region != OSMConfig.ALL_REGIONS[-1]:
                logger.info("Pausing for 30 seconds...")
                time.sleep(30)

        logger.info(f"\n{'=' * 60}")
        logger.info(f"Import completed: {len(successful_regions)} successful, "
                    f"{len(failed_regions)} failed")

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
                logger.info("Cleared all existing road segments")

            result = importer.import_region(region, clear_existing=False)

            if result["success"]:
                successful_regions.append(region)
                total_segments += result.get("segments_saved", 0)
                logger.info(f"{region}: {result['segments_saved']} segments")
            else:
                failed_regions.append(region)
                logger.error(f"{region} failed")

            if i < len(regions) - 1:
                time.sleep(10)

        return {
            "success": len(successful_regions) > 0,
            "successful_regions": successful_regions,
            "failed_regions": failed_regions,
            "total_segments": total_segments,
        }

    def _display_results(self, result: dict[str, Any]):
        """Display import results."""
        self.stdout.write("\n" + "=" * 60)

        if result.get("success"):
            self.stdout.write(self.style.SUCCESS("✅ IMPORT SUCCESSFUL"))

            if "successful_regions" in result:
                self.stdout.write(f"Regions imported: {len(result['successful_regions'])}")
                for region in result["successful_regions"][:5]:
                    self.stdout.write(f"  ✓ {region}")
                if len(result["successful_regions"]) > 5:
                    self.stdout.write(f"  ... and {len(result['successful_regions']) - 5} more")

            if "total_segments" in result:
                self.stdout.write(f"Segments saved: {result['total_segments']:,}")
        else:
            self.stdout.write(self.style.ERROR("IMPORT FAILED"))
            if "error" in result:
                self.stdout.write(f"Error: {result['error']}")

        # Show database status
        total_roads = RoadSegment.objects.count()
        roads_with_cost = RoadSegment.objects.filter(cost_time__gt=0).count()

        self.stdout.write("\n" + "-" * 60)
        self.stdout.write("DATABASE STATUS:")
        self.stdout.write(f"Total road segments: {total_roads:,}")
        self.stdout.write(f"Segments with costs: {roads_with_cost:,} ({roads_with_cost / total_roads * 100:.1f}%)")

        if total_roads > 0:
            avg_length = RoadSegment.objects.aggregate(avg=Avg('length_m'))['avg'] or 0
            avg_cost = RoadSegment.objects.aggregate(avg=Avg('cost_time'))['avg'] or 0
            self.stdout.write(f"Average length: {avg_length:.1f}m")
            self.stdout.write(f"Average time cost: {avg_cost:.1f}s")

        self.stdout.write("=" * 60)
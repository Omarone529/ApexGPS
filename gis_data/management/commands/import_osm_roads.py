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
    """Imports roads region by region."""

    def __init__(self):
        """Initialize importer."""
        self.api_client = OSMAPIClient()
        self.total_segments_imported = 0

    def import_region(
        self, region_name: str, clear_existing: bool = False
    ) -> dict[str, Any]:
        """Import roads for a single region."""
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

        for element in elements:
            if element.get("type") == "way":
                ways_processed += 1

                if "geometry" in element and len(element["geometry"]) >= 2:
                    ways_with_geometry += 1
                    segment = RoadDataProcessor.create_road_segment(element)
                    if segment:
                        segments.append(segment)

                if ways_processed % 1000 == 0:
                    logger.info(
                        f"Processed {ways_processed} ways,"
                        f" created {len(segments)} segments"
                    )
        logger.info(
            f"Total: processed {ways_processed} ways, "
            f"{ways_with_geometry} with geometry, created {len(segments)} segments"
        )

        if not segments:
            return {
                "success": False,
                "region": region_name,
                "error": "No valid road segments created",
            }

        saved_count = self._save_segments(segments, region_name, clear_existing)
        self.total_segments_imported += saved_count

        return {
            "success": True,
            "region": region_name,
            "elements_found": len(elements),
            "ways_processed": ways_processed,
            "ways_with_geometry": ways_with_geometry,
            "segments_created": len(segments),
            "segments_saved": saved_count,
        }

    def _save_segments(
        self, segments: list, region_name: str, clear_existing: bool
    ) -> int:
        """Save segments to database."""
        try:
            if clear_existing:
                deleted = RoadSegment.objects.filter(highway__isnull=False).delete()[0]
                logger.info(f"Cleared {deleted} existing road segments")

            with transaction.atomic():
                created_segments = RoadSegment.objects.bulk_create(
                    segments, ignore_conflicts=True, batch_size=1000
                )

            saved_count = len(created_segments)
            logger.info(f"Saved {saved_count} segments for {region_name}")
            return saved_count

        except Exception as e:
            logger.error(f"Failed to save segments: {e}")
            saved = 0
            batch_size = 100

            for i in range(0, len(segments), batch_size):
                batch = segments[i : i + batch_size]
                try:
                    with transaction.atomic():
                        RoadSegment.objects.bulk_create(batch, ignore_conflicts=True)
                    saved += len(batch)
                except Exception:
                    for segment in batch:
                        try:
                            segment.save()
                            saved += 1
                        except Exception:
                            pass

            return saved

    def import_test_only(self) -> dict[str, Any]:
        """Import only test data."""
        logger.info("TEST IMPORT ONLY - Small area")

        bbox = (41.89, 12.47, 41.90, 12.48)

        query = f"""
            [out:json][timeout:180];
            way["highway"~"primary|secondary|tertiary"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
            out geom;
        """

        data = self.api_client.execute_query(query)
        if not data:
            return {"success": False, "error": "Failed to fetch test data"}

        elements = data.get("elements", [])
        logger.info(f"Test got {len(elements)} elements")

        segments = []
        for element in elements:
            if element.get("type") == "way":
                segment = RoadDataProcessor.create_road_segment(element)
                if segment:
                    segments.append(segment)

        logger.info(f"Created {len(segments)} segments from test")

        if segments:
            saved = self._save_segments(segments, "test", True)
            return {
                "success": True,
                "segments_saved": saved,
                "message": f"Imported {saved} test road segments",
            }
        else:
            return {"success": False, "error": "No segments created from test data"}


class Command(BaseCommand):
    """Import OSM roads for Italian regions."""

    help = "Import road network from OpenStreetMap for Italian regions"

    def add_arguments(self, parser):
        """Add arguments to parser."""
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

    def handle(self, *args, **options):
        """Handle command execution."""
        if options["verbose"]:
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.INFO)

        importer = RegionalRoadImporter()

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
        """Import all regions."""
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
            self.stdout.write(self.style.SUCCESS("Import successful"))

            if "successful_regions" in result:
                self.stdout.write(
                    f"Regions imported: {len(result['successful_regions'])}"
                )

            if "total_segments" in result:
                self.stdout.write(f"Segments saved: {result['total_segments']:,}")
        else:
            self.stdout.write(self.style.ERROR("Import failed"))
            if "error" in result:
                self.stdout.write(f"Error: {result['error']}")

        total_roads = RoadSegment.objects.count()
        self.stdout.write(f"Total road segments in database: {total_roads:,}")

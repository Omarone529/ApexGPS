import gc
import logging
import time
from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection, transaction

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

    def __init__(self, batch_size=500):
        """Initialize importer."""
        self.api_client = OSMAPIClient()
        self.total_segments_imported = 0
        self.batch_size = batch_size

    def import_region(
        self, region_name: str, clear_existing: bool = False
    ) -> dict[str, Any]:
        """Import roads for a specific region."""
        logger.info(f"Importing roads for: {region_name}")

        # Add delay to avoid overloading OSM
        time.sleep(2)

        bbox = OSMConfig.REGION_BBOXES.get(region_name.lower(), OSMConfig.ITALY_BBOX)

        # For large regions, use simplified query
        if region_name in ["piemonte", "lombardia", "sicilia", "sardegna"]:
            logger.info(f"Large region {region_name}, using simplified query")
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

        try:
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

                    if ways_processed % 500 == 0:
                        logger.info(
                            f"Processed {ways_processed}/{way_count} ways, "
                            f"created {self.total_segments_imported} segments"
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

        except Exception as e:
            logger.error(f"Error during import: {str(e)}")
            return {
                "success": False,
                "region": region_name,
                "error": f"Import failed: {str(e)[:100]}",
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
                    segments, ignore_conflicts=True, batch_size=500
                )
            saved_count = len(created_segments)
            logger.info(f"Saved batch of {saved_count} segments for {region_name}")
            return saved_count

        except Exception as e:
            logger.error(f"Failed to save batch: {e}")

            # Fallback: save individually with smaller batch
            saved = 0
            for j in range(0, len(segments), 100):
                batch = segments[j : j + 100]
                for segment in batch:
                    try:
                        segment.save()
                        saved += 1
                    except Exception:
                        pass

            logger.info(f"Saved {saved} segments individually")
            return saved


class Command(BaseCommand):
    """Import complete region: roads, POIs, routing topology."""

    help = "Import a complete region with roads, POIs and routing topology"

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            "--force-region",
            type=str,
            help="Force import of a specific region",
        )
        parser.add_argument(
            "--clear", action="store_true", help="Clear existing data before import"
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Detailed output",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Batch size for processing (default: 500)",
        )
        parser.add_argument(
            "--skip-on-error",
            action="store_true",
            help="Continue even if import fails",
        )

    def get_next_region(self):
        """Get the next region to import completely."""
        # Import order: from smallest to largest regions
        priority_order = [
            "umbria",
            "valle_daosta",
            "molise",
            "basilicata",
            "friuli-venezia_giulia",
            "trentino-alto_adige",
            "marche",
            "abruzzo",
            "lazio",
            "liguria",
            "toscana",
            "emilia-romagna",
            "veneto",
            "campania",
            "puglia",
            "calabria",
            "piemonte",
            "lombardia",
            "sicilia",
            "sardegna",
        ]

        for region in priority_order:
            road_count = RoadSegment.objects.filter(region=region).count()
            if road_count < 50:
                logger.info(f"Region {region}: only {road_count} roads, needs import")
                return region

        logger.info("All regions appear to be already imported")
        return None

    def check_topology_exists(self):
        """Check if routing topology exists."""
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
                return cursor.fetchone()[0]
        except Exception:
            return False

    def handle(self, *args, **options):
        """Execute complete region import."""
        # Setup logging
        if options["verbose"]:
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.INFO)

        self.stdout.write("=" * 60)
        self.stdout.write("COMPLETE REGION IMPORT")
        self.stdout.write("=" * 60)

        # Check if we already have topology
        if self.check_topology_exists() and not options["clear"]:
            self.stdout.write("INFO: Topology already present in system")
            self.stdout.write("System ready for routing")
            return

        # Determine region to import
        if options["force_region"]:
            region_to_import = options["force_region"]
            self.stdout.write(f"Force importing region: {region_to_import}")
        else:
            region_to_import = self.get_next_region()

            if not region_to_import:
                self.stdout.write("INFO: All regions appear to be already imported")

                # Show current status
                total_roads = RoadSegment.objects.count()
                total_poi = 0
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT COUNT(*) FROM gis_data_pointofinterest")
                        total_poi = cursor.fetchone()[0]
                except Exception:
                    pass

                self.stdout.write("CURRENT STATUS:")
                self.stdout.write(f"  Total roads: {total_roads:,}")
                self.stdout.write(f"  Total POIs: {total_poi:,}")
                topology_status = (
                    "PRESENT" if self.check_topology_exists() else "ABSENT"
                )
                self.stdout.write(f"  Topology: {topology_status}")
                return

        self.stdout.write(f"Selected region: {region_to_import.upper()}")

        # Step 1: Import roads
        self.stdout.write("\n1. Importing roads...")
        importer = RegionalRoadImporter(batch_size=options["batch_size"])

        start_time = time.time()
        road_result = importer.import_region(
            region_to_import, clear_existing=options["clear"]
        )
        road_time = time.time() - start_time

        if not road_result["success"]:
            error_msg = road_result.get("error", "Unknown error")
            self.stderr.write(f"ERROR importing roads: {error_msg}")

            if not options["skip_on_error"]:
                self.stderr.write("IMPORT ABORTED")
                raise SystemExit(1) from None
            else:
                self.stdout.write("WARNING: Continuing despite error...")
                return
        else:
            segments_saved = road_result["segments_saved"]
            self.stdout.write(
                f"SUCCESS: Imported {segments_saved:,} roads in {road_time:.1f}s"
            )

        # Step 2: Import POIs
        self.stdout.write("\n2. Importing POIs...")
        try:
            poi_start = time.time()
            call_command(
                "import_osm_pois",
                area=region_to_import,
                categories="viewpoint,restaurant,church,historic",
                verbose=options["verbose"],
            )
            poi_time = time.time() - poi_start

            # Count POIs for this region
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT COUNT(*) FROM gis_data_pointofinterest
                        WHERE region = %s
                    """,
                        [region_to_import],
                    )
                    poi_count = cursor.fetchone()[0]
                    self.stdout.write(
                        f"SUCCESS: Imported {poi_count:,} POIs in {poi_time:.1f}s"
                    )
            except Exception:
                self.stdout.write("SUCCESS: POIs imported")

        except Exception as e:
            self.stderr.write(f"WARNING: Error importing POIs: {str(e)[:100]}")
            # Continue anyway

        # Step 3: Prepare GIS topology
        self.stdout.write("\n3. Preparing routing topology...")
        try:
            gis_start = time.time()
            call_command(
                "prepare_gis_data", area="italy", force=True, verbose=options["verbose"]
            )
            gis_time = time.time() - gis_start
            self.stdout.write(f"SUCCESS: Topology created in {gis_time:.1f}s")
        except Exception as e:
            self.stderr.write(f"CRITICAL ERROR preparing GIS: {str(e)[:100]}")
            if not options["skip_on_error"]:
                self.stderr.write("SYSTEM NOT READY FOR ROUTING")
                raise SystemExit(1) from e

        # Step 4: Final verification
        self.stdout.write("\n4. Final verification...")

        # Count roads for this region
        final_road_count = RoadSegment.objects.filter(region=region_to_import).count()

        # Count POIs for this region
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*) FROM gis_data_pointofinterest
                    WHERE region = %s
                """,
                    [region_to_import],
                )
                final_poi_count = cursor.fetchone()[0]
        except Exception:
            final_poi_count = 0

        # Check topology
        has_topology = self.check_topology_exists()

        # Display summary
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("IMPORT SUMMARY")
        self.stdout.write("=" * 60)
        self.stdout.write(f"Region: {region_to_import.upper()}")
        self.stdout.write(f"Roads imported: {final_road_count:,}")
        self.stdout.write(f"POIs imported: {final_poi_count:,}")
        self.stdout.write(
            f"Routing topology: {'PRESENT' if has_topology else 'ABSENT'}"
        )

        # Check if region is ready for routing
        if final_road_count > 50 and has_topology:
            self.stdout.write("\nSTATUS: REGION READY FOR ROUTING")

            # Total stats
            total_roads = RoadSegment.objects.count()
            total_regions = RoadSegment.objects.values("region").distinct().count()

            self.stdout.write("\nTOTAL STATISTICS:")
            self.stdout.write(f"Regions imported: {total_regions}/20")
            self.stdout.write(f"Total roads: {total_roads:,}")

        else:
            self.stdout.write("\nWARNING: Incomplete region")

            if final_road_count <= 50:
                self.stdout.write(f"  - Too few roads: {final_road_count} (minimum 50)")
            if not has_topology:
                self.stdout.write("  - Topology not created")

        self.stdout.write("=" * 60)

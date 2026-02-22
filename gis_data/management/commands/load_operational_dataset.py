import gc
import logging
import time
from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection, transaction

from gis_data.models import RoadSegment, PointOfInterest, RoadSegmentPOIRelation
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

        time.sleep(2)

        bbox = OSMConfig.REGION_BBOXES.get(region_name.lower(), OSMConfig.ITALY_BBOX)

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

                    if len(segments) >= self.batch_size:
                        saved = self._save_segments_batch(
                            segments, region_name, clear_existing and i == 0
                        )
                        self.total_segments_imported += saved
                        segments = []
                        gc.collect()

                    if ways_processed % 500 == 0:
                        logger.info(
                            f"Processed {ways_processed}/{way_count} ways, "
                            f"created {self.total_segments_imported} segments"
                        )

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

            saved = 0
            for j in range(0, len(segments), 100):
                batch = segments[j: j + 100]
                for segment in batch:
                    try:
                        segment.save()
                        saved += 1
                    except Exception:
                        pass

            logger.info(f"Saved {saved} segments individually")
            return saved


class Command(BaseCommand):
    """Import complete region: roads, POIs, routing topology, POI relations, and DEM data."""

    help = "Import a complete region with roads, POIs, routing topology, POI relations and DEM data"

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
        parser.add_argument(
            "--run-all",
            action="store_true",
            help="Import all remaining regions in sequence",
        )
        parser.add_argument(
            "--show-status",
            action="store_true",
            help="Show current import status without importing",
        )
        parser.add_argument(
            "--skip-dems",
            action="store_true",
            help="Skip DEM data preparation",
        )
        parser.add_argument(
            "--skip-poi-relations",
            action="store_true",
            help="Skip POI relations pre-computation",
        )
        parser.add_argument(
            "--dem-resolutions",
            type=int,
            default=300,
            help="Resolution for DEM tiles (default: 300)",
        )

    REGION_TO_DEM_TILE = {
        "piemonte": "nord_ovest",
        "valle_daosta": "nord_ovest",
        "liguria": "nord_ovest",
        "lombardia": "nord_ovest",
        "trentino-alto_adige": "nord_est",
        "veneto": "nord_est",
        "friuli-venezia_giulia": "nord_est",
        "emilia-romagna": "nord_est",
        "toscana": "centro_ovest",
        "umbria": "centro_ovest",
        "marche": "centro_est",
        "lazio": "centro_ovest",
        "abruzzo": "centro_est",
        "molise": "centro_est",
        "campania": "sud_ovest",
        "puglia": "sud_est",
        "basilicata": "sud_ovest",
        "calabria": "sud_ovest",
        "sicilia": "sicilia",
        "sardegna": "sardegna",
    }

    def get_next_region(self):
        """
        Determine the next region to import by checking the database directly.
        A region is considered "complete" if it has roads AND POIs.
        """
        priority_order = [
            "umbria", "valle_daosta", "molise", "basilicata",
            "friuli-venezia_giulia", "trentino-alto_adige", "marche",
            "abruzzo", "lazio", "liguria", "toscana", "emilia-romagna",
            "veneto", "campania", "puglia", "calabria", "piemonte",
            "lombardia", "sicilia", "sardegna",
        ]

        logger.info("Looking for next region to import in database...")

        for region in priority_order:
            road_count = RoadSegment.objects.filter(region=region).count()
            poi_count = PointOfInterest.objects.filter(region=region).count()

            has_roads = road_count > 50
            has_pois = poi_count > 0

            if not has_roads:
                logger.info(f"Region {region}: no roads (or <50), selected")
                return region
            elif not has_pois:
                logger.info(f"Region {region}: roads present ({road_count}) but no POIs, selected")
                return region
            else:
                logger.info(f"Region {region}: complete ({road_count} roads, {poi_count} POIs)")

        logger.info("All regions are complete!")
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

    def prepare_dem_for_region(self, region: str, options):
        """Prepare DEM data specifically for a region."""
        if options["skip_dems"]:
            self.stdout.write("  Skipping DEM data preparation (--skip-dems)")
            return True

        dem_tile = self.REGION_TO_DEM_TILE.get(region)
        if not dem_tile:
            self.stdout.write(f"  No DEM tile mapping for {region}, skipping")
            return True

        self.stdout.write(f"  Preparing DEM tile: {dem_tile} for region {region}")

        try:
            call_command(
                "prepare_dem_data",
                area=dem_tile,
                table_name="dem_data",
                force=options["clear"],
                resolution=options["dem_resolutions"],
                verbose=options["verbose"],
            )
            return True
        except Exception as e:
            self.stderr.write(f"  WARNING: DEM preparation failed for {region}: {str(e)[:100]}")
            if not options["skip_on_error"]:
                return False
            return True

    def import_single_region(self, region_to_import, options):
        """Import a single region completely."""
        self.stdout.write(f"\n{'=' * 60}")
        self.stdout.write(f"IMPORTING REGION: {region_to_import.upper()}")
        self.stdout.write('=' * 60)

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
                return False
        else:
            segments_saved = road_result["segments_saved"]
            self.stdout.write(
                f"Imported {segments_saved:,} roads in {road_time:.1f}s"
            )

        # Step 2: Import POIs
        self.stdout.write("\n2. Importing POIs...")
        try:
            poi_start = time.time()
            call_command(
                "import_osm_pois",
                area=region_to_import,
                region=region_to_import,
                categories="viewpoint,restaurant,church,historic",
                verbose=options["verbose"],
            )
            poi_time = time.time() - poi_start

            poi_count = PointOfInterest.objects.filter(region=region_to_import).count()
            self.stdout.write(f"Imported {poi_count:,} POIs in {poi_time:.1f}s")

        except Exception as e:
            self.stderr.write(f"WARNING: Error importing POIs: {str(e)[:100]}")
            poi_count = PointOfInterest.objects.filter(region=region_to_import).count()
            if poi_count > 0:
                self.stdout.write(f"Found {poi_count:,} existing POIs for this region")
            else:
                self.stdout.write("No POIs were imported for this region")

        # Step 3: Prepare GIS topology
        self.stdout.write("\n3. Preparing routing topology...")
        try:
            gis_start = time.time()
            call_command(
                "prepare_gis_data", area="italy", force=True, verbose=options["verbose"]
            )
            gis_time = time.time() - gis_start
            self.stdout.write(f"Topology created in {gis_time:.1f}s")
        except Exception as e:
            self.stderr.write(f"CRITICAL ERROR preparing GIS: {str(e)[:100]}")
            if not options["skip_on_error"]:
                self.stderr.write("SYSTEM NOT READY FOR ROUTING")
                raise SystemExit(1) from e

        # Step 3.5: Pre-compute POI relations
        if not options["skip_poi_relations"]:
            self.stdout.write("\n3.5. Pre-computing POI-road relations...")
            try:
                rel_start = time.time()
                call_command(
                    "precompute_poi_relations",
                    region=region_to_import,
                    max_distance=2500,
                    verbose=options["verbose"],
                )
                rel_time = time.time() - rel_start

                # Count relations
                rel_count = RoadSegmentPOIRelation.objects.filter(
                    road_segment__region=region_to_import
                ).count()
                self.stdout.write(f"Created {rel_count:,} POI relations in {rel_time:.1f}s")
            except Exception as e:
                self.stderr.write(f"WARNING: Error pre-computing POI relations: {str(e)[:100]}")
                if not options["skip_on_error"]:
                    self.stderr.write("CONTINUING WITH NEXT STEPS...")
        else:
            self.stdout.write("\n3.5. Skipping POI relations (--skip-poi-relations)")

        # Step 4: Prepare DEM for this region
        self.stdout.write("\n4. Preparing DEM data for region...")
        dem_success = self.prepare_dem_for_region(region_to_import, options)
        if not dem_success and not options["skip_on_error"]:
            self.stderr.write("DEM preparation failed")
            return False

        # Step 5: Final verification
        self.stdout.write("\n5. Final verification...")
        final_road_count = RoadSegment.objects.filter(region=region_to_import).count()
        final_poi_count = PointOfInterest.objects.filter(region=region_to_import).count()
        final_rel_count = RoadSegmentPOIRelation.objects.filter(
            road_segment__region=region_to_import
        ).count() if not options["skip_poi_relations"] else 0
        has_topology = self.check_topology_exists()

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("IMPORT SUMMARY")
        self.stdout.write("=" * 60)
        self.stdout.write(f"Region: {region_to_import.upper()}")
        self.stdout.write(f"Roads imported: {final_road_count:,}")
        self.stdout.write(f"POIs imported: {final_poi_count:,}")
        if not options["skip_poi_relations"]:
            self.stdout.write(f"POI relations: {final_rel_count:,}")
        self.stdout.write(f"Topology: {'PRESENT' if has_topology else 'ABSENT'}")
        self.stdout.write(f"DEM: {'PREPARED' if dem_success else 'SKIPPED/FAILED'}")

        if final_road_count > 50 and has_topology:
            self.stdout.write("\nSTATUS: REGION READY FOR ROUTING")
        else:
            self.stdout.write("\nWARNING: INCOMPLETE REGION")
            if final_road_count <= 50:
                self.stdout.write(f"  - Too few roads: {final_road_count} (min 50)")
            if not has_topology:
                self.stdout.write("  - Topology not created")

        self.stdout.write("=" * 60)
        return True

    def show_import_status(self):
        """Show import status for all regions."""
        priority_order = [
            "umbria", "valle_daosta", "molise", "basilicata",
            "friuli-venezia_giulia", "trentino-alto_adige", "marche",
            "abruzzo", "lazio", "liguria", "toscana", "emilia-romagna",
            "veneto", "campania", "puglia", "calabria", "piemonte",
            "lombardia", "sicilia", "sardegna",
        ]

        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("REGION IMPORT STATUS")
        self.stdout.write("=" * 70)

        total_complete = 0
        total_relations = RoadSegmentPOIRelation.objects.count()

        for region in priority_order:
            road_count = RoadSegment.objects.filter(region=region).count()
            poi_count = PointOfInterest.objects.filter(region=region).count()
            rel_count = RoadSegmentPOIRelation.objects.filter(
                road_segment__region=region
            ).count()

            has_roads = road_count > 50
            has_pois = poi_count > 0
            has_rels = rel_count > 0

            if has_roads and has_pois and has_rels:
                marker = "✓"
                total_complete += 1
                status = "complete"
            elif has_roads and has_pois:
                marker = "◔"
                status = "needs relations"
            elif has_roads and not has_pois:
                marker = "◔"
                status = "roads only"
            elif not has_roads and has_pois:
                marker = "◔"
                status = "POIs only"
            else:
                marker = "○"
                status = "not imported"

            self.stdout.write(
                f"{marker} {region:22} : {status:15} - "
                f"{road_count:6,} roads, {poi_count:4,} POIs, {rel_count:6,} rels"
            )

        self.stdout.write("-" * 70)
        self.stdout.write(f"Complete regions: {total_complete}/{len(priority_order)}")
        self.stdout.write(f"Total POI relations: {total_relations:,}")

        # Check DEM table
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_name = 'dem_data'
                    )
                """
                )
                has_dem = cursor.fetchone()[0]
                if has_dem:
                    cursor.execute("SELECT COUNT(*) FROM dem_data")
                    dem_count = cursor.fetchone()[0]
                    self.stdout.write(f"DEM table: PRESENT ({dem_count} tiles)")
                else:
                    self.stdout.write("DEM table: ABSENT")
        except Exception as e:
            self.stdout.write(f"DEM table: ERROR CHECKING - {str(e)[:50]}")

        self.stdout.write("=" * 70)

    def import_all_regions(self, options):
        """Import all regions in sequence."""
        self.stdout.write("\nSequential import of all regions")

        count = 0
        processed_tiles = set()

        while True:
            region = self.get_next_region()
            if not region:
                break

            self.stdout.write(f"\n--- Import #{count + 1}: {region} ---")
            success = self.import_single_region(region, options)

            if success:
                count += 1
                # Track which DEM tiles processed
                dem_tile = self.REGION_TO_DEM_TILE.get(region)
                if dem_tile:
                    processed_tiles.add(dem_tile)
                self.stdout.write(f"Completed {count} regions so far")

            if self.get_next_region():
                self.stdout.write("Pausing 30 seconds before next region...")
                time.sleep(30)

        self.stdout.write(f"\n{'=' * 70}")
        self.stdout.write(f"IMPORT COMPLETED: {count} regions processed")
        self.stdout.write(f"DEM tiles prepared: {len(processed_tiles)}")
        if processed_tiles:
            self.stdout.write(f"Tiles: {', '.join(sorted(processed_tiles))}")
        self.stdout.write('=' * 70)

        self.show_import_status()

    def handle(self, *args, **options):
        """Execute complete region import."""
        if options["verbose"]:
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.INFO)

        self.stdout.write("=" * 70)
        self.stdout.write("COMPLETE REGION IMPORT - ROADS, POIS, TOPOLOGY, RELATIONS, DEM")
        self.stdout.write("=" * 70)

        if options["show_status"]:
            self.show_import_status()
            return

        if options["run_all"]:
            self.import_all_regions(options)
            return

        if self.check_topology_exists() and not options["clear"]:
            self.stdout.write("INFO: Topology already present")
            self.stdout.write("System ready for routing")
            self.show_import_status()
            return

        if options["force_region"]:
            region_to_import = options["force_region"]
            self.stdout.write(f"Force importing region: {region_to_import}")
        else:
            region_to_import = self.get_next_region()

            if not region_to_import:
                self.stdout.write("INFO: All regions are already complete")
                self.show_import_status()
                return

        self.import_single_region(region_to_import, options)
        self.show_import_status()
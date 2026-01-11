import logging
import os
from typing import Any

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection

from gis_data.models import PointOfInterest, RoadSegment
from gis_data.utils.osm_utils import OSMConfig

logger = logging.getLogger(__name__)


class DatabaseStatusChecker:
    """Checker for database status with regional import awareness."""

    def __init__(self):
        """Initialize the DatabaseStatusChecker."""
        self.roads_table = self._get_model_table("gis_data", "RoadSegment")
        self.pois_table = self._get_model_table("gis_data", "PointOfInterest")

    def _get_model_table(self, app_label: str, model_name: str) -> str | None:
        """Get database table name for a model."""
        try:
            model = apps.get_model(app_label, model_name)
            return model._meta.db_table
        except LookupError:
            logger.warning(f"Model not found: {app_label}.{model_name}")
            return None

    def _safe_table_exists(self, table_name: str) -> bool:
        """Safely check if a table exists."""
        if not table_name:
            return False

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_schema = 'public'
                        AND table_name = %s
                    )
                """,
                    [table_name],
                )
                return cursor.fetchone()[0]
        except Exception:
            return False

    def _get_row_count(self, table_name: str) -> int:
        """Safely get row count for a table."""
        if not table_name or not self._safe_table_exists(table_name):
            return 0

        try:
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                return cursor.fetchone()[0]
        except Exception:
            return 0

    def _get_scenic_cost_count(self) -> int:
        """Count road segments with scenic costs calculated."""
        if not self.roads_table:
            return 0

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT COUNT(*) FROM {self.roads_table}
                    WHERE cost_scenic IS NOT NULL AND cost_scenic != 0
                """
                )
                return cursor.fetchone()[0]
        except Exception:
            return 0

    def _check_topology_exists(self) -> bool:
        """Check if routing topology exists."""
        if not self.roads_table:
            return False

        topology_table = f"{self.roads_table}_vertices_pgr"
        return self._safe_table_exists(topology_table)

    def _check_italian_regions_coverage(self) -> tuple[int, list[str]]:
        """Check how many Italian regions have been imported."""
        if not self.roads_table:
            return 0, []

        try:
            with connection.cursor() as cursor:
                # Count distinct OSM IDs as a proxy for data coverage
                cursor.execute(f"SELECT COUNT(DISTINCT osm_id) FROM {self.roads_table}")
                osm_count = cursor.fetchone()[0]

                # Estimate regions based on road count
                if osm_count > 200000:
                    estimated_regions = len(OSMConfig.ALL_REGIONS)
                elif osm_count > 50000:
                    estimated_regions = len(OSMConfig.ALL_REGIONS) // 2
                elif osm_count > 10000:
                    estimated_regions = 5
                elif osm_count > 1000:
                    estimated_regions = 1
                else:
                    estimated_regions = 0

                return (
                    estimated_regions,
                    OSMConfig.ALL_REGIONS[:estimated_regions]
                    if estimated_regions > 0
                    else [],
                )

        except Exception:
            return 0, []

    def check_status(self) -> dict[str, Any]:
        """Check current status of database preparation."""
        status = {"status": "unknown", "message": "", "details": {}}

        # Check if road segments table exists
        if not self._safe_table_exists(self.roads_table):
            status.update(
                {
                    "status": "not_ready",
                    "message": "Road segments table does not exist.",
                    "next_action": "Run migrations: python manage.py migrate",
                }
            )
            return status

        # Check if road segments have data
        road_count = self._get_row_count(self.roads_table)
        if road_count == 0:
            status.update(
                {
                    "status": "not_ready",
                    "message": "No road segments found in database.",
                    "road_count": 0,
                    "next_action": "Import roads: python manage.py "
                    "import_osm_roads --regions all --sequential --verbose",
                }
            )
            return status

        # Check region coverage
        regions_covered, covered_list = self._check_italian_regions_coverage()

        # Check if there is sufficient data for routing
        if road_count < 10000:  # Minimum for basic routing
            status.update(
                {
                    "status": "insufficient_data",
                    "message": f"Only {road_count:,} road segments found from"
                    f" {regions_covered} regions. "
                    f"Need at least 10,000 segments for basic routing.",
                    "road_count": road_count,
                    "regions_covered": regions_covered,
                    "covered_regions": covered_list,
                    "next_action": "Import more regions: python manage.py "
                    "import_osm_roads --regions all --sequential --verbose",
                }
            )
            return status

        # Check if topology exists
        if not self._check_topology_exists():
            status.update(
                {
                    "status": "partially_ready",
                    "message": f"Roads exist ({road_count:,} "
                    f"segments from {regions_covered} "
                    f"regions) but topology not created.",
                    "road_count": road_count,
                    "regions_covered": regions_covered,
                    "covered_regions": covered_list,
                    "next_action": "Prepare GIS data: python manage.py "
                    "prepare_gis_data --area italy --verbose --force",
                }
            )
            return status

        # Check if costs are calculated
        scenic_cost_count = self._get_scenic_cost_count()
        if scenic_cost_count == 0:
            status.update(
                {
                    "status": "partially_ready",
                    "message": f"Topology exists"
                    f" ({road_count:,} segments) but routing costs not calculated.",
                    "road_count": road_count,
                    "regions_covered": regions_covered,
                    "next_action": "Prepare GIS data: python manage.py "
                    "prepare_gis_data --area italy --verbose --force",
                }
            )
            return status

        # Check if POIs exist
        if not self._safe_table_exists(self.pois_table):
            status.update(
                {
                    "status": "ready_without_pois",
                    "message": f"Routing ready"
                    f" ({road_count:,} segments, {regions_covered} "
                    f"regions) but no POIs imported.",
                    "road_count": road_count,
                    "regions_covered": regions_covered,
                    "scenic_costs_calculated": scenic_cost_count,
                    "next_action": "Import POIs: python manage.py "
                    "import_osm_pois --area italy --verbose",
                }
            )
            return status

        poi_count = self._get_row_count(self.pois_table)
        if poi_count == 0:
            status.update(
                {
                    "status": "ready_without_pois",
                    "message": "Routing ready but POI table is empty.",
                    "road_count": road_count,
                    "regions_covered": regions_covered,
                    "scenic_costs_calculated": scenic_cost_count,
                    "next_action": "Import POIs: python manage.py "
                    "import_osm_pois --area italy --verbose",
                }
            )
            return status

        # Check if there are enough POIs
        if poi_count < 1000:
            status.update(
                {
                    "status": "ready_with_limited_pois",
                    "message": f"Routing ready but only {poi_count:,} POIs found. "
                    f"Recommended > 10,000 for Italy.",
                    "road_count": road_count,
                    "poi_count": poi_count,
                    "regions_covered": regions_covered,
                    "scenic_costs_calculated": scenic_cost_count,
                    "next_action": "Import more POIs: python manage.py "
                    "import_osm_pois --area italy --verbose",
                }
            )
            return status

        # Database is fully ready
        status.update(
            {
                "status": "fully_ready",
                "message": f"Database is fully prepared for routing operations. "
                f"{road_count:,} road segments from {regions_covered} regions, "
                f"{poi_count:,} POIs, scenic costs calculated.",
                "road_count": road_count,
                "poi_count": poi_count,
                "regions_covered": regions_covered,
                "covered_regions": covered_list,
                "scenic_costs_calculated": scenic_cost_count,
            }
        )

        return status


class RoadImportManager:
    """Manager for regional road imports."""

    def __init__(self, area: str):
        """Initialize the RoadImportManager."""
        self.area = area
        self.imported = False
        self.road_count_before = 0
        self.road_count_after = 0
        self.regions_to_import = []

    def _get_regions_to_import(self, test_mode: bool = False) -> list[str]:
        """Determine which regions to import based on area and mode."""
        if test_mode:
            return ["test"]

        area_lower = self.area.lower()

        if area_lower == "italy" or area_lower == "all":
            # Import only initial regions, not all
            return OSMConfig.INITIAL_REGIONS
        elif area_lower in OSMConfig.REGION_BBOXES:
            return [area_lower]
        else:
            # Try to match partial region name
            matching_regions = [r for r in OSMConfig.INITIAL_REGIONS if area_lower in r]
            return (
                matching_regions if matching_regions else OSMConfig.INITIAL_REGIONS[:1]
            )

    def needs_import(
        self, force: bool, current_status: str, test_mode: bool = False
    ) -> bool:
        """Determine if road import is needed."""
        self.road_count_before = RoadSegment.objects.count()
        self.regions_to_import = self._get_regions_to_import(test_mode)

        if force:
            return True

        if current_status in ["not_ready", "insufficient_data"]:
            return True

        # Lower thresholds for initial setup
        if self.area.lower() in ["italy", "all"]:
            return self.road_count_before < 5000  # Need at least 5000 segments
        else:
            return self.road_count_before < 1000

    def run_import(self, force: bool, test_mode: bool = False) -> bool:
        """Run regional road import."""
        try:
            if test_mode:
                args = ["import_osm_roads", "--test-only", "--verbose"]
            elif (
                len(self.regions_to_import) == 1 and self.regions_to_import[0] != "test"
            ):
                # Single region
                args = [
                    "import_osm_roads",
                    "--regions",
                    self.regions_to_import[0],
                    "--verbose",
                ]
            else:
                # Multiple initial regions
                regions_arg = ",".join(self.regions_to_import)
                args = [
                    "import_osm_roads",
                    "--regions",
                    regions_arg,
                    "--sequential",
                    "--verbose",
                ]

            if force:
                args.append("--clear")

            logger.info(f"Running road import with args: {args}")
            call_command(*args)
            self.imported = True
            self.road_count_after = RoadSegment.objects.count()

            logger.info(f"Road import completed: {self.road_count_after:,} segments")

            return True
        except Exception as e:
            logger.error(f"Road import failed: {e}")
            return False

    def get_stats(self) -> dict[str, Any]:
        """Get import statistics."""
        return {
            "imported": self.imported,
            "road_count_before": self.road_count_before,
            "road_count_after": self.road_count_after,
            "roads_added": self.road_count_after - self.road_count_before,
            "regions_imported": self.regions_to_import,
        }


class POIImportManager:
    """Manager for POI imports using environment configuration."""

    def __init__(self, area: str):
        """Initialize the POIImportManager."""
        self.area = area
        self.imported = False
        self.poi_count_before = 0
        self.poi_count_after = 0

    def needs_import(
        self, force: bool, skip_pois: bool, current_status: str, test_mode: bool = False
    ) -> bool:
        """Determine if POI import is needed."""
        if skip_pois:
            return False

        self.poi_count_before = PointOfInterest.objects.count()

        if force:
            return True

        if current_status in [
            "ready_without_pois",
            "ready_with_limited_pois",
            "not_ready",
            "partially_ready",
            "insufficient_data",
        ]:
            return True

        # For test mode, we need fewer POIs
        if test_mode:
            return self.poi_count_before < 100
        else:
            return self.poi_count_before < 1000

    def run_import(self, force: bool, test_mode: bool = False) -> bool:
        """Run POI import using environment configuration."""
        try:
            if test_mode:
                # For test mode, use a small area
                area = "test"
                categories = "viewpoint,restaurant"
            else:
                area = self.area
                categories = "viewpoint,restaurant,church,historic"

            args = [
                "import_osm_pois",
                "--area",
                area,
                "--categories",
                categories,
                "--verbose",
            ]

            logger.info(f"Running POI import with args: {args}")
            call_command(*args)
            self.imported = True
            self.poi_count_after = PointOfInterest.objects.count()
            logger.info(f"POI import completed: {self.poi_count_after:,} points")

            return True
        except Exception as e:
            logger.error(f"POI import failed: {e}")
            return False

    def get_stats(self) -> dict[str, Any]:
        """Get import statistics."""
        return {
            "imported": self.imported,
            "poi_count_before": self.poi_count_before,
            "poi_count_after": self.poi_count_after,
            "pois_added": self.poi_count_after - self.poi_count_before,
        }


class GISPreparationManager:
    """Manager for GIS data preparation."""

    def __init__(self, area: str):
        """Initialize the GISPreparationManager."""
        self.area = area
        self.prepared = False

    def needs_preparation(self, force: bool, current_status: str) -> bool:
        """Determine if GIS preparation is needed."""
        if force:
            return True

        return current_status in [
            "partially_ready",
            "insufficient_data",
            "ready_without_pois",
            "ready_with_limited_pois",
        ]

    def run_preparation(self, force: bool) -> bool:
        """Run GIS data preparation."""
        try:
            # Get area from environment or use default
            init_area = os.environ.get("INIT_AREA", "italy")
            area_to_use = self.area if self.area != "test" else init_area

            args = ["prepare_gis_data", "--area", area_to_use, "--verbose"]
            if force:
                args.append("--force")

            logger.info(f"Running GIS preparation with args: {args}")
            call_command(*args)
            self.prepared = True
            return True
        except Exception as e:
            logger.error(f"GIS preparation failed: {e}")
            return False

    def get_stats(self) -> dict[str, Any]:
        """Get preparation statistics."""
        return {
            "prepared": self.prepared,
        }


class SetupPipeline:
    """Main pipeline for database setup with regional import support."""

    def __init__(
        self, area: str, force: bool, skip_pois: bool, test_mode: bool = False
    ):
        """Initialize the SetupPipeline."""
        self.area = area
        self.force = force
        self.skip_pois = skip_pois
        self.test_mode = test_mode

        # Adjust area for test mode
        if test_mode:
            self.effective_area = "test"
        else:
            self.effective_area = area

        self.status_checker = DatabaseStatusChecker()
        self.road_import_manager = RoadImportManager(self.effective_area)
        self.poi_import_manager = POIImportManager(self.effective_area)
        self.gis_preparation_manager = GISPreparationManager(self.effective_area)

        self.stats = {
            "initial_status": None,
            "final_status": None,
            "steps_completed": [],
            "step_details": {},
            "errors": [],
            "test_mode": test_mode,
            "area": self.effective_area,
        }

    def check_initial_status(self):
        """Check initial database status."""
        self.stats["initial_status"] = self.status_checker.check_status()
        status_info = self.stats["initial_status"]
        logger.info(f"Initial status: {status_info['status']}")

    def run_road_import(self) -> bool:
        """Run road import if needed."""
        current_status = self.stats["initial_status"]["status"]

        if self.road_import_manager.needs_import(
            self.force, current_status, self.test_mode
        ):
            if self.test_mode:
                logger.info("Starting test road import...")
            else:
                logger.info("Starting regional road import...")

            if self.road_import_manager.run_import(self.force, self.test_mode):
                logger.info("Road import completed")
                self.stats["step_details"][
                    "roads"
                ] = self.road_import_manager.get_stats()
                return True
            else:
                self.stats["errors"].append("Road import failed")
                return False
        else:
            road_count = self.road_import_manager.road_count_before
            logger.info(f"Roads already exist: {road_count:,} segments")
            return True

    def run_poi_import(self) -> bool:
        """Run POI import if needed."""
        current_status = self.stats["initial_status"]["status"]

        if self.poi_import_manager.needs_import(
            self.force, self.skip_pois, current_status, self.test_mode
        ):
            if self.test_mode:
                logger.info("Starting test POI import...")
            else:
                logger.info("Starting POI import...")

            if self.poi_import_manager.run_import(self.force, self.test_mode):
                logger.info("POI import completed")
                self.stats["step_details"]["pois"] = self.poi_import_manager.get_stats()
                return True
            else:
                self.stats["errors"].append("POI import failed")
                return False
        else:
            poi_count = self.poi_import_manager.poi_count_before
            logger.info(f"POIs already exist: {poi_count:,} points")
            return True

    def run_gis_preparation(self) -> bool:
        """Run GIS preparation if needed."""
        current_status = self.stats["initial_status"]["status"]

        if self.gis_preparation_manager.needs_preparation(self.force, current_status):
            logger.info("Preparing GIS data...")

            if self.gis_preparation_manager.run_preparation(self.force):
                logger.info("GIS data preparation completed")
                self.stats["step_details"][
                    "gis"
                ] = self.gis_preparation_manager.get_stats()
                return True
            else:
                self.stats["errors"].append("GIS preparation failed")
                return False
        else:
            logger.info("GIS data already prepared")
            return True

    def check_final_status(self):
        """Check final database status."""
        self.stats["final_status"] = self.status_checker.check_status()
        final_status = self.stats["final_status"]
        logger.info(f"Final status: {final_status['status']}")

    def run(self) -> dict[str, Any]:
        """Run the complete setup pipeline."""
        result = {"success": False, "stats": self.stats.copy()}

        try:
            # Check initial status
            self.check_initial_status()

            # Import roads (always needed for routing)
            if not self.run_road_import():
                logger.error("Road import step failed")
                return result

            # Import POIs (if not skipped)
            if not self.skip_pois and not self.run_poi_import():
                logger.error("POI import step failed")
                # Continue anyway, as routing can work without POIs

            # Prepare GIS data (critical for routing)
            if not self.run_gis_preparation():
                logger.error("GIS preparation step failed")
                return result

            # Check final status
            self.check_final_status()

            result["success"] = True
            result["stats"] = self.stats.copy()

        except Exception as e:
            self.stats["errors"].append(str(e))
            logger.error(f"Setup pipeline error: {e}")

        return result


class Command(BaseCommand):
    """Command for database setup with regional import support."""

    help = (
        "Check and setup database for scenic routing operations with regional imports"
    )

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            "--check-only",
            action="store_true",
            help="Only check database status without performing setup",
        )
        parser.add_argument(
            "--force", action="store_true", help="Force re-import of all data"
        )
        parser.add_argument(
            "--area",
            type=str,
            default=None,
            help="Geographic area to import",
        )
        parser.add_argument("--skip-pois", action="store_true", help="Skip POI import")
        parser.add_argument(
            "--verbose", action="store_true", help="Show detailed output"
        )
        parser.add_argument(
            "--test-mode", action="store_true", help="Run in test mode (small imports)"
        )
        parser.add_argument(
            "--regions-only",
            action="store_true",
            help="Import only roads (regions) without POIs or GIS preparation",
        )
        parser.add_argument(
            "--pois-only",
            action="store_true",
            help="Import only POIs without roads or GIS preparation",
        )

    def setup_logging(self, verbose: bool):
        """Setup logging based on verbosity."""
        if verbose:
            logging.basicConfig(
                level=logging.DEBUG,
                format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            )
        else:
            logging.basicConfig(
                level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
            )

    def get_target_area(self, options) -> str:
        """Determine the target area for import."""
        # Priority: command argument > environment variable > default
        if options["area"]:
            return options["area"]

        env_area = os.environ.get("INIT_AREA")
        if env_area:
            return env_area

        return "italy"

    def display_header(
        self, area: str, test_mode: bool, regions_only: bool, pois_only: bool
    ):
        """Display command header."""
        self.stdout.write("=" * 70)
        self.stdout.write("DATABASE SETUP FOR SCENIC ROUTING - REGIONAL IMPORT")
        self.stdout.write("=" * 70)

        if test_mode:
            self.stdout.write(self.style.WARNING("TEST MODE ENABLED"))

        self.stdout.write(f"\nTarget Area: {area.upper()}")

        if regions_only:
            self.stdout.write("Mode: Regions import only (roads)")
        elif pois_only:
            self.stdout.write("Mode: POIs import only")
        else:
            self.stdout.write("Mode: Full setup")

        self.stdout.write("-" * 70)

    def display_status_check(self, status: dict[str, Any]):
        """Display database status check results."""
        self.stdout.write(f"\nDATABASE STATUS: {status['status'].upper()}")
        self.stdout.write(f"Message: {status['message']}")

        if "road_count" in status:
            self.stdout.write(f"Road segments: {status['road_count']:,}")

        if "poi_count" in status:
            self.stdout.write(f"POIs: {status['poi_count']:,}")

        if "regions_covered" in status and status["regions_covered"] > 0:
            self.stdout.write(f"Regions covered: {status['regions_covered']}")
            if "covered_regions" in status and status["covered_regions"]:
                self.stdout.write(
                    f"Covered regions: {', '.join(status['covered_regions'][:5])}"
                )
                if len(status["covered_regions"]) > 5:
                    self.stdout.write(
                        f"  ... and {len(status['covered_regions']) - 5} more"
                    )

        if "scenic_costs_calculated" in status:
            self.stdout.write(
                f"Scenic costs calculated: {status['scenic_costs_calculated']:,}"
            )

        if "next_action" in status:
            self.stdout.write(f"\nRecommended action: {status['next_action']}")

    def display_setup_results(self, result: dict[str, Any]):
        """Display setup results."""
        stats = result["stats"]

        self.stdout.write("\n" + "=" * 70)

        if result["success"]:
            self.stdout.write(
                self.style.SUCCESS("✓ DATABASE SETUP COMPLETED SUCCESSFULLY!")
            )
        else:
            self.stdout.write(self.style.ERROR("✗ DATABASE SETUP FAILED!"))

        # Display test mode info
        if stats.get("test_mode"):
            self.stdout.write(
                self.style.WARNING("\n[TEST MODE] - Limited data imported")
            )

        # Display completed steps
        if stats["steps_completed"]:
            self.stdout.write(f"\nSteps completed ({len(stats['steps_completed'])}):")
            for step in stats["steps_completed"]:
                self.stdout.write(f"  ✓ {step}")

        # Display step details
        if stats.get("step_details"):
            self.stdout.write("\nDetails:")

            if "roads" in stats["step_details"]:
                road_stats = stats["step_details"]["roads"]
                if road_stats.get("imported"):
                    self.stdout.write(
                        f"Roads:" f" {road_stats.get('road_count_after', 0):,} segments"
                    )
                    if road_stats.get("roads_added", 0) > 0:
                        self.stdout.write(
                            f"Added:"
                            f" {road_stats.get('roads_added', 0):,} new segments"
                        )
                    if road_stats.get("regions_imported"):
                        self.stdout.write(
                            f"Regions: {', '.join(road_stats['regions_imported'][:3])}"
                        )
                        if len(road_stats["regions_imported"]) > 3:
                            self.stdout.write(
                                f"{len(road_stats['regions_imported']) - 3} more"
                            )

            if "pois" in stats["step_details"]:
                poi_stats = stats["step_details"]["pois"]
                if poi_stats.get("imported"):
                    self.stdout.write(
                        f"  POIs: {poi_stats.get('poi_count_after', 0):,} points"
                    )
                    if poi_stats.get("pois_added", 0) > 0:
                        self.stdout.write(
                            f"    Added: {poi_stats.get('pois_added', 0):,} new points"
                        )

            if "gis" in stats["step_details"]:
                gis_stats = stats["step_details"]["gis"]
                if gis_stats.get("prepared"):
                    self.stdout.write("  GIS: Data prepared for routing")

        # Display final status
        if stats.get("final_status"):
            final = stats["final_status"]
            self.stdout.write(f"\nFinal Status: {final['status'].upper()}")
            self.stdout.write(f"{final['message']}")

            if "road_count" in final:
                self.stdout.write(f"Road segments: {final['road_count']:,}")

            if "poi_count" in final:
                self.stdout.write(f"POIs: {final['poi_count']:,}")

            if "regions_covered" in final and final["regions_covered"] > 0:
                self.stdout.write(
                    f"Italian regions covered: {final['regions_covered']}/20"
                )

            if "scenic_costs_calculated" in final:
                self.stdout.write(
                    f"Scenic costs calculated: {final['scenic_costs_calculated']:,}"
                )

        # Display errors
        if stats["errors"]:
            self.stdout.write(f"\nErrors encountered ({len(stats['errors'])}):")
            for error in stats["errors"]:
                self.stdout.write(f"  ✗ {error}")

        # Display recommendations
        self.stdout.write("\n" + "-" * 70)

        if result["success"]:
            final_status = stats.get("final_status", {}).get("status", "")

            if final_status == "fully_ready":
                self.stdout.write(
                    self.style.SUCCESS("READY: System is fully operational!")
                )
                self.stdout.write(
                    "You can now start the server and use the routing API."
                )
            elif final_status == "ready_with_limited_pois":
                self.stdout.write(
                    self.style.WARNING(
                        "READY: System operational but with limited POIs."
                    )
                )
                self.stdout.write(
                    "Consider importing more POIs for better scenic routing."
                )
            elif final_status == "ready_without_pois":
                self.stdout.write(
                    self.style.WARNING("READY: Routing works but without POIs.")
                )
                self.stdout.write("Run without --skip-pois to import POIs.")
            elif final_status == "partially_ready":
                self.stdout.write(
                    self.style.WARNING("PARTIAL: Some steps may need attention.")
                )
                self.stdout.write("Check the details above for recommendations.")
        else:
            self.stdout.write(self.style.ERROR("NOT READY: Setup failed."))
            self.stdout.write(
                "Check the errors above and try again with --verbose flag."
            )

        self.stdout.write("=" * 70)

    def handle(self, *args, **options):
        """Handle command execution."""
        # Setup logging
        self.setup_logging(options["verbose"])

        # Get target area
        target_area = self.get_target_area(options)

        # Display header
        self.display_header(
            target_area,
            options["test_mode"],
            options["regions_only"],
            options["pois_only"],
        )

        # Check-only mode
        if options["check_only"]:
            status_checker = DatabaseStatusChecker()
            status = status_checker.check_status()
            self.display_status_check(status)
            return

        # Special modes
        if options["regions_only"]:
            # Import only regions/roads
            self.stdout.write(f"\nImporting roads for area: {target_area}...")
            road_manager = RoadImportManager(target_area)
            if road_manager.run_import(options["force"], options["test_mode"]):
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ Imported {road_manager.road_count_after:,} road segments"
                    )
                )
                return
            else:
                self.stdout.write(self.style.ERROR("✗ Road import failed"))
                raise SystemExit(1)

        elif options["pois_only"]:
            # Import only POIs
            self.stdout.write(f"\nImporting POIs for area: {target_area}...")
            poi_manager = POIImportManager(target_area)
            if poi_manager.run_import(options["force"], options["test_mode"]):
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ Imported {poi_manager.poi_count_after:,} POIs"
                    )
                )
                return
            else:
                self.stdout.write(self.style.ERROR("✗ POI import failed"))
                raise SystemExit(1)

        # Full setup mode
        self.stdout.write(f"\nStarting full database setup for area: {target_area}...")

        # Run setup pipeline
        pipeline = SetupPipeline(
            area=target_area,
            force=options["force"],
            skip_pois=options["skip_pois"],
            test_mode=options["test_mode"],
        )

        result = pipeline.run()

        # Display results
        self.display_setup_results(result)

        # Exit with appropriate code
        if not result["success"]:
            raise SystemExit(1)

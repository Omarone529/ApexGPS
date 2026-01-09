import logging
import traceback
from typing import Any

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection

from gis_data.models import PointOfInterest, RoadSegment

logger = logging.getLogger(__name__)


class DatabaseStatusChecker:
    """Checker for database status."""

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
        except Exception as e:
            logger.debug(f"Error checking table {table_name}: {e}")
            return False

    def _get_row_count(self, table_name: str) -> int:
        """Safely get row count for a table."""
        if not table_name or not self._safe_table_exists(table_name):
            return 0

        try:
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                return cursor.fetchone()[0]
        except Exception as e:
            logger.warning(f"Error counting rows in {table_name}: {e}")
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
        except Exception as e:
            logger.warning(f"Error counting scenic costs: {e}")
            return 0

    def _check_topology_exists(self) -> bool:
        """Check if routing topology exists."""
        if not self.roads_table:
            return False

        topology_table = f"{self.roads_table}_vertices_pgr"
        return self._safe_table_exists(topology_table)

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
                    "import_osm_roads --area italy --clear --verbose",
                }
            )
            return status

        # Check if there are enough data for Italy
        if road_count < 10000:
            status.update(
                {
                    "status": "insufficient_data",
                    "message": f"Only {road_count:,} road segments found. "
                    "Italy should have > 100,000.",
                    "road_count": road_count,
                    "next_action": "Re-import roads: python manage.py "
                    "import_osm_roads --area italy --clear --verbose",
                }
            )
            return status

        # Check if topology exists
        if not self._check_topology_exists():
            status.update(
                {
                    "status": "partially_ready",
                    "message": "Roads exist but topology not created.",
                    "road_count": road_count,
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
                    "message": "Topology exists but routing costs not calculated.",
                    "road_count": road_count,
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
                    "message": "Routing ready but no POIs imported.",
                    "road_count": road_count,
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
                    "scenic_costs_calculated": scenic_cost_count,
                    "next_action": "Import POIs: python manage.py "
                    "import_osm_pois --area italy --verbose",
                }
            )
            return status

        # Check if there are  enough POIs for Italy
        if poi_count < 1000:
            status.update(
                {
                    "status": "insufficient_pois",
                    "message": f"Only {poi_count:,} POIs found. "
                    "Italy should have > 10,000.",
                    "road_count": road_count,
                    "poi_count": poi_count,
                    "scenic_costs_calculated": scenic_cost_count,
                    "next_action": "Re-import POIs: python manage.py "
                    "import_osm_pois --area italy --clear --verbose",
                }
            )
            return status

        # Database is fully ready
        status.update(
            {
                "status": "fully_ready",
                "message": "Database is fully prepared for routing operations.",
                "road_count": road_count,
                "poi_count": poi_count,
                "scenic_costs_calculated": scenic_cost_count,
            }
        )

        return status


class RoadImportManager:
    """Manager for road imports."""

    def __init__(self, area: str):
        """Initialize the RoadImportManager."""
        self.area = area
        self.imported = False
        self.road_count_before = 0
        self.road_count_after = 0

    def needs_import(self, force: bool, current_status: str) -> bool:
        """Determine if road import is needed."""
        self.road_count_before = RoadSegment.objects.count()

        if force:
            return True

        if current_status in ["not_ready", "insufficient_data"]:
            return True

        return self.road_count_before < 10000  # Se meno di 10k, reimporta

    def run_import(self, force: bool) -> bool:
        """Run road import."""
        try:
            args = ["import_osm_roads", "--area", self.area, "--verbose"]
            if force:
                args.append("--clear")

            call_command(*args)
            self.imported = True
            self.road_count_after = RoadSegment.objects.count()
            logger.info(f"Road import completed: {self.road_count_after:,} segments")
            if self.road_count_after < 10000 and self.area == "italy":
                logger.warning(
                    f"Warning: Only {self.road_count_after:,}"
                    f" segments imported for Italy"
                )
                logger.warning(
                    "Expected > 100,000 segments. The import may have failed."
                )

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
        }


class POIImportManager:
    """Manager for POI imports."""

    def __init__(self, area: str):
        """Initialize the POIImportManager."""
        self.area = area
        self.imported = False
        self.poi_count_before = 0
        self.poi_count_after = 0

    def needs_import(self, force: bool, skip_pois: bool, current_status: str) -> bool:
        """Determine if POI import is needed."""
        if skip_pois:
            return False

        self.poi_count_before = PointOfInterest.objects.count()

        if force:
            return True

        if current_status in [
            "ready_without_pois",
            "not_ready",
            "partially_ready",
            "insufficient_pois",
            "insufficient_data",
        ]:
            return True

        # if there are few, re-import
        return self.poi_count_before < 1000

    def run_import(self, force: bool) -> bool:
        """Run POI import."""
        try:
            args = [
                "import_osm_pois",
                "--area",
                self.area,
                "--categories",
                "viewpoint,restaurant,church,historic",
                "--verbose",
            ]
            if force:
                args.append("--clear")

            call_command(*args)
            self.imported = True
            self.poi_count_after = PointOfInterest.objects.count()
            logger.info(f"POI import completed: {self.poi_count_after:,} points")

            if self.poi_count_after < 1000 and self.area == "italy":
                logger.warning(
                    f"Warning: Only {self.poi_count_after:,} POIs imported for Italy"
                )
                logger.warning("Expected > 10,000 POIs.")

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

        return current_status in ["partially_ready", "insufficient_data"]

    def run_preparation(self, force: bool) -> bool:
        """Run GIS data preparation."""
        try:
            args = ["prepare_gis_data", "--area", "italy", "--verbose"]
            if force:
                args.append("--force")

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
    """Main pipeline for database setup."""

    def __init__(self, area: str, force: bool, skip_pois: bool):
        """Initialize the SetupPipeline."""
        self.area = area
        self.force = force
        self.skip_pois = skip_pois

        self.status_checker = DatabaseStatusChecker()
        self.road_import_manager = RoadImportManager(area)
        self.poi_import_manager = POIImportManager(area)
        self.gis_preparation_manager = GISPreparationManager(area)

        self.stats = {
            "initial_status": None,
            "final_status": None,
            "steps_completed": [],
            "step_details": {},
            "errors": [],
        }

    def check_initial_status(self):
        """Check initial database status."""
        self.stats["initial_status"] = self.status_checker.check_status()
        logger.info(f"Initial status: {self.stats['initial_status']['status']}")

    def run_road_import(self) -> bool:
        """Run road import if needed."""
        current_status = self.stats["initial_status"]["status"]

        if self.road_import_manager.needs_import(self.force, current_status):
            self.stats["steps_completed"].append("Starting road import...")

            if self.road_import_manager.run_import(self.force):
                self.stats["steps_completed"].append("Road import completed")
                self.stats["step_details"][
                    "roads"
                ] = self.road_import_manager.get_stats()
                return True
            else:
                self.stats["errors"].append("Road import failed")
                return False
        else:
            road_count = self.road_import_manager.road_count_before
            self.stats["steps_completed"].append(
                f"Roads already exist: {road_count:,} segments"
            )
            return True

    def run_poi_import(self) -> bool:
        """Run POI import if needed."""
        current_status = self.stats["initial_status"]["status"]

        if self.poi_import_manager.needs_import(
            self.force, self.skip_pois, current_status
        ):
            self.stats["steps_completed"].append("Starting POI import...")

            if self.poi_import_manager.run_import(self.force):
                self.stats["steps_completed"].append("POI import completed")
                self.stats["step_details"]["pois"] = self.poi_import_manager.get_stats()
                return True
            else:
                self.stats["errors"].append("POI import failed")
                return False
        else:
            poi_count = self.poi_import_manager.poi_count_before
            self.stats["steps_completed"].append(
                f"POIs already exist: {poi_count:,} points"
            )
            return True

    def run_gis_preparation(self) -> bool:
        """Run GIS preparation if needed."""
        current_status = self.stats["initial_status"]["status"]

        if self.gis_preparation_manager.needs_preparation(self.force, current_status):
            self.stats["steps_completed"].append("Preparing GIS data...")

            if self.gis_preparation_manager.run_preparation(self.force):
                self.stats["steps_completed"].append("GIS data preparation completed")
                self.stats["step_details"][
                    "gis"
                ] = self.gis_preparation_manager.get_stats()
                return True
            else:
                self.stats["errors"].append("GIS preparation failed")
                return False
        else:
            self.stats["steps_completed"].append("GIS data already prepared")
            return True

    def check_final_status(self):
        """Check final database status."""
        self.stats["final_status"] = self.status_checker.check_status()
        logger.info(f"Final status: {self.stats['final_status']['status']}")

    def run(self) -> dict[str, Any]:
        """Run the complete setup pipeline."""
        result = {"success": False, "stats": self.stats.copy()}

        try:
            # Check initial status
            self.check_initial_status()

            # Import roads
            if not self.run_road_import():
                return result

            # Import POIs (if not skipped)
            if not self.skip_pois and not self.run_poi_import():
                return result

            # Prepare GIS data
            if not self.run_gis_preparation():
                return result

            # Check final status
            self.check_final_status()

            result["success"] = True
            result["stats"] = self.stats.copy()

        except Exception as e:
            self.stats["errors"].append(str(e))
            logger.error(f"Setup pipeline error: {e}")
            logger.error(traceback.format_exc())

        return result


class Command(BaseCommand):
    """Command for database setup."""

    help = "Check and setup database for scenic routing operations"

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
            default="italy",
            help="Geographic area to import (default: italy)",
        )
        parser.add_argument("--skip-pois", action="store_true", help="Skip POI import")
        parser.add_argument(
            "--verbose", action="store_true", help="Show detailed output"
        )
        parser.add_argument(
            "--test-mode", action="store_true", help="Run in test mode (small imports)"
        )

    def setup_logging(self, verbose: bool):
        """Setup logging based on verbosity."""
        if verbose:
            logging.basicConfig(level=logging.INFO)
        else:
            logging.basicConfig(level=logging.WARNING)

    def display_header(self, area: str):
        """Display command header."""
        self.stdout.write("=" * 60)
        self.stdout.write("Database Setup for Scenic Routing")
        self.stdout.write("=" * 60)
        self.stdout.write(f"\nArea: {area}")
        self.stdout.write("-" * 40)

    def display_status_check(self, status: dict[str, Any]):
        """Display database status check results."""
        self.stdout.write(f"\nDatabase Status: {status['status'].upper()}")
        self.stdout.write(f"Message: {status['message']}")

        if "road_count" in status:
            self.stdout.write(f"Road segments: {status['road_count']:,}")

        if "poi_count" in status:
            self.stdout.write(f"POIs: {status['poi_count']:,}")

        if "scenic_costs_calculated" in status:
            self.stdout.write(
                f"Scenic costs calculated: {status['scenic_costs_calculated']:,}"
            )

        if "next_action" in status:
            self.stdout.write(f"\nNext action: {status['next_action']}")

    def display_setup_progress(self, step: str):
        """Display setup progress."""
        self.stdout.write(f"\n{step}")

    def display_setup_results(self, result: dict[str, Any]):
        """Display setup results."""
        stats = result["stats"]

        self.stdout.write("\n" + "=" * 60)

        if result["success"]:
            self.stdout.write(
                self.style.SUCCESS("Database setup completed successfully!")
            )

            # Display completed steps
            if stats["steps_completed"]:
                self.stdout.write(
                    f"\nSteps completed ({len(stats['steps_completed'])}):"
                )
                for step in stats["steps_completed"]:
                    self.stdout.write(f"{step}")

            # Display final status
            if stats["final_status"]:
                final = stats["final_status"]
                self.stdout.write(f"\nFinal Status: {final['status'].upper()}")
                self.stdout.write(f"{final['message']}")

                if "road_count" in final:
                    self.stdout.write(f"Road segments: {final['road_count']:,}")

                if "poi_count" in final:
                    self.stdout.write(f"POIs: {final['poi_count']:,}")

                if "scenic_costs_calculated" in final:
                    self.stdout.write(
                        f"Scenic costs: {final['scenic_costs_calculated']:,}"
                    )

        else:
            self.stdout.write(self.style.ERROR("Database setup failed!"))

            if stats["errors"]:
                self.stdout.write(f"\nErrors encountered ({len(stats['errors'])}):")
                for error in stats["errors"]:
                    self.stdout.write(f"  ✗ {error}")

            # Display partial statistics if available
            if stats.get("step_details"):
                self.stdout.write("\nPartial results:")
                if "roads" in stats["step_details"]:
                    road_stats = stats["step_details"]["roads"]
                    self.stdout.write(
                        f"  Roads: {road_stats.get('road_count_after', 0):,}"
                    )

                if "pois" in stats["step_details"]:
                    poi_stats = stats["step_details"]["pois"]
                    self.stdout.write(
                        f"  POIs: {poi_stats.get('poi_count_after', 0):,}"
                    )

        self.stdout.write("=" * 60)

    def handle(self, *args, **options):
        """Handle command execution."""
        # Setup logging
        self.setup_logging(options["verbose"])

        # Display header
        self.display_header(options["area"])

        # Create status checker
        status_checker = DatabaseStatusChecker()

        # Check-only mode
        if options["check_only"]:
            status = status_checker.check_status()
            self.display_status_check(status)
            return

        # Setup mode
        self.stdout.write(f"\nStarting database setup for area: {options['area']}...")

        # Adjust area for test mode
        area = options["area"]
        if options["test_mode"]:
            area = "test"
            self.stdout.write(
                self.style.WARNING(
                    f"Test mode enabled, using area:"
                    f" {area} instead of {options['area']}"
                )
            )

        # Run setup pipeline
        pipeline = SetupPipeline(
            area=area, force=options["force"], skip_pois=options["skip_pois"]
        )

        result = pipeline.run()

        # Display results
        self.display_setup_results(result)

        # Exit with appropriate code
        if not result["success"]:
            raise SystemExit(1)

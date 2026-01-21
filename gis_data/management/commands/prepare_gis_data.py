import logging
import sys
import time
from typing import Any

from django.core.management.base import BaseCommand
from django.db import connection

from gis_data.services.metrics_calculator import MetricsCalculator
from gis_data.services.topology_service import TopologyService

logger = logging.getLogger(__name__)

__all__ = [
    "DatabaseExtensionManager",
    "RoadDataValidator",
    "RoutingCostCalculator",
    "MetricsPipeline",
    "ValidationReporter",
    "GISPreparationPipeline",
    "Command",
]


class DatabaseExtensionManager:
    """Manager for PostgreSQL extensions."""

    @staticmethod
    def check_extension_exists(extension_name: str) -> tuple[bool, str | None]:
        """Check if a PostgreSQL extension is installed."""
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT extname, extversion FROM pg_extension WHERE extname = %s",
                    [extension_name],
                )
                result = cursor.fetchone()
                if result:
                    return True, result[1]
                return False, None
        except Exception as e:
            logger.error(f"Error checking extension {extension_name}: {e}")
            return False, None

    @staticmethod
    def install_pgrouting_extension() -> bool:
        """Install pgRouting extension if not present."""
        try:
            with connection.cursor() as cursor:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS pgrouting")
                logger.info("pgRouting extension installed/verified")
                return True
        except Exception as e:
            logger.error(f"Cannot install pgrouting: {e}")
            return False

    @staticmethod
    def verify_required_extensions() -> bool:
        """Verify required PostgreSQL extensions are installed."""
        required_extensions = [
            ("postgis", True, None),
            ("postgis_topology", True, None),
            ("pgrouting", True, DatabaseExtensionManager.install_pgrouting_extension),
        ]

        all_ok = True

        for ext_name, required, install_func in required_extensions:
            exists, version = DatabaseExtensionManager.check_extension_exists(ext_name)

            if exists:
                logger.info(f"Extension {ext_name} is installed (version {version})")
                continue

            if required and install_func:
                if install_func():
                    logger.info(f"Extension {ext_name} installed successfully")
                    continue
                else:
                    logger.error(f"Failed to install extension {ext_name}")
                    all_ok = False
            elif required:
                logger.error(f"Required extension {ext_name} not installed")
                all_ok = False

        return all_ok


class RoadDataValidator:
    """Validator for road data."""

    @staticmethod
    def count_road_segments() -> int:
        """Count road segments with valid geometry."""
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*) FROM gis_data_roadsegment
                    WHERE geometry IS NOT NULL
                    """
                )
                return cursor.fetchone()[0]
        except Exception as e:
            logger.error(f"Error counting road segments: {e}")
            return 0

    @staticmethod
    def is_database_empty() -> bool:
        """Check if the database contains road data."""
        count = RoadDataValidator.count_road_segments()

        if count == 0:
            logger.warning("No road segments found in database")
            return True

        logger.info(f"Found {count} road segments")
        return False

    @staticmethod
    def get_road_data_summary() -> dict[str, Any]:
        """Get summary statistics for road data."""
        summary = {
            "total_segments": 0,
            "segments_with_geometry": 0,
            "segments_without_geometry": 0,
            "by_highway_type": {},
        }

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM gis_data_roadsegment")
                summary["total_segments"] = cursor.fetchone()[0]

                cursor.execute(
                    "SELECT COUNT(*) FROM gis_data_roadsegment "
                    "WHERE geometry IS NOT NULL"
                )
                summary["segments_with_geometry"] = cursor.fetchone()[0]
                summary["segments_without_geometry"] = (
                    summary["total_segments"] - summary["segments_with_geometry"]
                )

                cursor.execute(
                    """
                    SELECT highway, COUNT(*)
                    FROM gis_data_roadsegment
                    WHERE highway IS NOT NULL
                    GROUP BY highway
                    ORDER BY COUNT(*) DESC
                    LIMIT 10
                """
                )
                for row in cursor.fetchall():
                    summary["by_highway_type"][row[0]] = row[1]

        except Exception as e:
            logger.error(f"Error getting road data summary: {e}")

        return summary


class RoutingCostCalculator:
    """Calculator for routing costs."""

    @staticmethod
    def calculate_accurate_lengths() -> int:
        """Calculate accurate geographic lengths for road segments."""
        try:
            with connection.cursor() as cursor:
                logger.info("Starting to calculate accurate road lengths...")
                start_time = time.time()
                cursor.execute(
                    """
                    UPDATE gis_data_roadsegment
                    SET length_m = ST_Length(geometry::geography)
                    WHERE geometry IS NOT NULL
                    AND (length_m = 0 OR length_m IS NULL)
                """
                )
                updated = cursor.rowcount
                elapsed = time.time() - start_time
                logger.info(
                    f"Updated length for {updated} segments in {elapsed:.1f} seconds"
                )
                return updated
        except Exception as e:
            logger.error(f"Error calculating lengths: {e}")
            return 0

    @staticmethod
    def calculate_time_costs() -> int:
        """Calculate time-based routing costs."""
        try:
            with connection.cursor() as cursor:
                logger.info("Starting to calculate time-based routing costs...")
                start_time = time.time()
                cursor.execute(
                    """
                    UPDATE gis_data_roadsegment
                    SET cost_time =
                        CASE
                            WHEN maxspeed > 0 THEN length_m / (maxspeed / 3.6)
                            ELSE length_m / (50 / 3.6)
                        END
                    WHERE length_m > 0
                """
                )
                updated = cursor.rowcount
                elapsed = time.time() - start_time
                logger.info(
                    f"Calculated time costs for {updated} "
                    f"segments in {elapsed:.1f} seconds"
                )
                return updated
        except Exception as e:
            logger.error(f"Error calculating time costs: {e}")
            return 0

    @staticmethod
    def calculate_length_costs() -> int:
        """Calculate distance-based routing costs."""
        try:
            with connection.cursor() as cursor:
                logger.info("Starting to calculate distance-based routing costs...")
                start_time = time.time()
                cursor.execute(
                    """
                    UPDATE gis_data_roadsegment
                    SET cost_length = length_m
                    WHERE length_m > 0
                """
                )
                updated = cursor.rowcount
                elapsed = time.time() - start_time
                logger.info(
                    f"Calculated length costs for {updated}"
                    f" segments in {elapsed:.1f} seconds"
                )
                return updated
        except Exception as e:
            logger.error(f"Error calculating length costs: {e}")
            return 0

    @staticmethod
    def calculate_scenic_costs() -> int:
        """Calculate scenic routing costs."""
        try:
            with connection.cursor() as cursor:
                logger.info("Starting to calculate scenic routing costs...")
                start_time = time.time()
                cursor.execute(
                    """
                    UPDATE gis_data_roadsegment
                    SET cost_scenic =
                        CASE
                            WHEN scenic_rating IS NOT NULL THEN
                                (0.3 * length_m) - (0.7 * (scenic_rating * 100))
                            ELSE length_m
                        END
                    WHERE length_m > 0
                """
                )
                updated = cursor.rowcount
                elapsed = time.time() - start_time
                logger.info(
                    f"Calculated scenic costs for {updated} "
                    f"segments in {elapsed:.1f} seconds"
                )
                return updated
        except Exception as e:
            logger.error(f"Error calculating scenic costs: {e}")
            return 0

    @staticmethod
    def calculate_balanced_costs() -> int:
        """Calculate balanced routing costs."""
        try:
            with connection.cursor() as cursor:
                logger.info("Starting to calculate balanced routing costs...")
                start_time = time.time()
                cursor.execute(
                    """
                    UPDATE gis_data_roadsegment
                    SET cost_balanced =
                        CASE
                            WHEN scenic_rating IS NOT NULL THEN
                                (0.6 * length_m) - (0.4 * (scenic_rating * 100))
                            ELSE length_m
                        END
                    WHERE length_m > 0
                """
                )
                updated = cursor.rowcount
                elapsed = time.time() - start_time
                logger.info(
                    f"Calculated balanced costs for"
                    f" {updated} segments in {elapsed:.1f} seconds"
                )
                return updated
        except Exception as e:
            logger.error(f"Error calculating balanced costs: {e}")
            return 0


class MetricsPipeline:
    """Pipeline for calculating all metrics."""

    def __init__(self, metrics_calculator: MetricsCalculator):
        """Init function."""
        self.metrics_calculator = metrics_calculator
        self.cost_calculator = RoutingCostCalculator()
        self.stats = {
            "core_metrics_calculated": False,
            "scenic_scores_calculated": False,
            "lengths_updated": 0,
            "time_costs_calculated": 0,
            "length_costs_calculated": 0,
            "scenic_costs_calculated": 0,
            "balanced_costs_calculated": 0,
        }

    def calculate_core_metrics(self) -> bool:
        """Calculate core road metrics."""
        try:
            logger.info("Starting core metrics calculation...")
            start_time = time.time()

            result = self.metrics_calculator.calculate_core_metrics()

            elapsed = time.time() - start_time
            logger.info(f"Core metrics calculated in {elapsed:.1f} seconds")

            if isinstance(result, dict) and "average_length_m" in result:
                logger.info(
                    f"Average segment length: {result['average_length_m']:.1f}m"
                )
                logger.info(
                    f"Average curvature: {result.get('average_curvature', 0):.3f}"
                )

            self.stats["core_metrics_calculated"] = True
            return True
        except Exception as e:
            logger.error(f"Error calculating core metrics: {e}")
            return False

    def calculate_scenic_scores(self) -> bool:
        """Calculate scenic scores."""
        try:
            logger.info("Starting scenic scores calculation...")
            start_time = time.time()

            result = self.metrics_calculator.calculate_scenic_scores()

            elapsed = time.time() - start_time
            logger.info(f"Scenic scores calculated in {elapsed:.1f} seconds")

            if isinstance(result, dict) and "average_scenic_rating" in result:
                logger.info(
                    f"Average scenic rating: {result['average_scenic_rating']:.2f}"
                )
                logger.info(
                    f"Highly scenic segments: {result.get('highly_scenic_segments', 0)}"
                )

            self.stats["scenic_scores_calculated"] = True
            return True
        except Exception as e:
            logger.error(f"Error calculating scenic scores: {e}")
            return False

    def calculate_routing_costs(self) -> bool:
        """Calculate all routing costs."""
        success = True

        logger.info("Starting routing costs calculation...")
        total_start_time = time.time()

        # Update lengths
        logger.info("Calculating accurate lengths...")
        updated = self.cost_calculator.calculate_accurate_lengths()
        self.stats["lengths_updated"] = updated
        logger.info(f"Lengths updated for {updated} segments")

        # Calculate various costs
        logger.info("Calculating time-based costs...")
        time_costs = self.cost_calculator.calculate_time_costs()
        self.stats["time_costs_calculated"] = time_costs

        logger.info("Calculating distance-based costs...")
        length_costs = self.cost_calculator.calculate_length_costs()
        self.stats["length_costs_calculated"] = length_costs

        logger.info("Calculating scenic costs...")
        scenic_costs = self.cost_calculator.calculate_scenic_costs()
        self.stats["scenic_costs_calculated"] = scenic_costs

        logger.info("Calculating balanced costs...")
        balanced_costs = self.cost_calculator.calculate_balanced_costs()
        self.stats["balanced_costs_calculated"] = balanced_costs

        total_elapsed = time.time() - total_start_time
        logger.info(f"All routing costs calculated in {total_elapsed:.1f} seconds")

        logger.info(
            f"Summary: {time_costs} time costs, {length_costs} length costs, "
            f"{scenic_costs} scenic costs, {balanced_costs} balanced costs"
        )

        if time_costs == 0 or length_costs == 0:
            success = False
            logger.error("Failed to calculate essential routing costs")

        return success

    def run(self) -> dict:
        """Run the complete metrics pipeline."""
        result = {"success": False, "errors": [], "stats": self.stats.copy()}

        try:
            logger.info("=" * 60)
            logger.info("STARTING METRICS PIPELINE")
            logger.info("=" * 60)

            pipeline_start = time.time()

            # Core metrics
            logger.info("--- PHASE 1: Core Metrics ---")
            if not self.calculate_core_metrics():
                result["errors"].append("Failed to calculate core metrics")

            # Scenic scores
            logger.info("--- PHASE 2: Scenic Scores ---")
            if not self.calculate_scenic_scores():
                result["errors"].append("Failed to calculate scenic scores")

            # Routing costs
            logger.info("--- PHASE 3: Routing Costs ---")
            if not self.calculate_routing_costs():
                result["errors"].append("Failed to calculate routing costs")

            # Determine overall success
            if (
                self.stats["core_metrics_calculated"]
                and self.stats["scenic_scores_calculated"]
                and self.stats["time_costs_calculated"] > 0
            ):
                result["success"] = True

            pipeline_elapsed = time.time() - pipeline_start
            logger.info("=" * 60)
            logger.info(
                f"METRICS PIPELINE COMPLETED IN" f" {pipeline_elapsed:.1f} SECONDS"
            )
            logger.info(f"Success: {result['success']}")
            logger.info("=" * 60)

            result["stats"] = self.stats.copy()

        except Exception as e:
            logger.error(f"Pipeline error: {e}")
            result["errors"].append(f"Pipeline error: {e}")

        return result


class ValidationReporter:
    """Reporter for validation results."""

    def __init__(
        self, topology_service: TopologyService, metrics_calculator: MetricsCalculator
    ):
        """Init function."""
        self.topology_service = topology_service
        self.metrics_calculator = metrics_calculator
        self.validation_results = {}
        self.summary_results = {}

    def get_topology_validation(self):
        """Get topology validation results."""
        try:
            logger.info("Starting topology validation...")
            self.validation_results[
                "topology"
            ] = self.topology_service.validate_topology()
            logger.info("Topology validation completed")

            if self.validation_results["topology"]:
                logger.info(
                    f"Topology is valid:"
                    f" {self.validation_results['topology'].get('is_valid', False)}"
                )
        except Exception as e:
            logger.warning(f"Topology validation failed: {e}")
            self.validation_results["topology"] = None

    def get_topology_summary(self):
        """Get topology summary statistics."""
        try:
            logger.info("Getting topology summary...")
            self.summary_results[
                "topology"
            ] = self.topology_service.get_topology_summary()
            logger.info("Topology summary obtained")

            if self.summary_results["topology"]:
                logger.info(
                    f"Total segments:"
                    f" {self.summary_results['topology'].get('total_segments', 0)}"
                )
                logger.info(
                    f"Routable segments:"
                    f" {self.summary_results['topology'].get('routable_segments', 0)}"
                )
        except Exception as e:
            logger.warning(f"Could not get topology summary: {e}")
            self.summary_results["topology"] = None

    def get_metrics_summary(self):
        """Get metrics summary statistics."""
        try:
            logger.info("Getting metrics summary...")
            self.summary_results[
                "metrics"
            ] = self.metrics_calculator.get_metrics_summary()
            logger.info("Metrics summary obtained")
        except Exception as e:
            logger.warning(f"Could not get metrics summary: {e}")
            self.summary_results["metrics"] = None

    def display_summary(self, stdout):
        """Display summary to stdout."""
        stdout.write("\n" + "=" * 60)
        stdout.write("VALIDATION SUMMARY")
        stdout.write("=" * 60)

        if self.summary_results.get("topology"):
            stdout.write("\nTopology:")
            for key, value in self.summary_results["topology"].items():
                stdout.write(f"  {key}: {value}")

        if self.summary_results.get("metrics"):
            stdout.write("\nMetrics:")
            for key, value in self.summary_results["metrics"].items():
                stdout.write(f"  {key}: {value}")

    def run(self) -> dict:
        """Run all validations and reports."""
        logger.info("Starting validation reporter...")
        self.get_topology_validation()
        self.get_topology_summary()
        self.get_metrics_summary()
        logger.info("Validation reporter completed")

        return {
            "validation": self.validation_results,
            "summary": self.summary_results,
        }


class GISPreparationPipeline:
    """Main pipeline for GIS data preparation."""

    def __init__(
        self, area: str, tolerance: float, force_rebuild: bool, skip_metrics: bool
    ):
        """Init function."""
        self.area = area
        self.tolerance = tolerance
        self.force_rebuild = force_rebuild
        self.skip_metrics = skip_metrics
        self.topology_service = TopologyService()
        self.metrics_calculator = MetricsCalculator()
        self.extension_manager = DatabaseExtensionManager()
        self.data_validator = RoadDataValidator()
        self.stats = {
            "extensions_verified": False,
            "data_valid": False,
            "topology_created": False,
            "metrics_calculated": False,
            "validation_completed": False,
        }

    def verify_extensions(self) -> bool:
        """Verify required PostgreSQL extensions."""
        logger.info("Verifying PostgreSQL extensions...")
        self.stats[
            "extensions_verified"
        ] = self.extension_manager.verify_required_extensions()
        logger.info(f"Extensions verified: {self.stats['extensions_verified']}")
        return self.stats["extensions_verified"]

    def validate_data(self) -> bool:
        """Validate that we have road data to process."""
        logger.info("Validating road data...")
        is_empty = self.data_validator.is_database_empty()
        self.stats["data_valid"] = not is_empty

        if is_empty:
            summary = self.data_validator.get_road_data_summary()
            logger.warning(f"Road data summary: {summary}")
            logger.error("No valid road data found")
        else:
            count = self.data_validator.count_road_segments()
            logger.info(f"Valid road data found: {count} segments")

        logger.info(f"Data valid: {self.stats['data_valid']}")
        return self.stats["data_valid"]

    def create_topology(self) -> bool:
        """Create routing topology."""
        try:
            logger.info(
                f"Creating routing topology"
                f" (tolerance: {self.tolerance}, force: {self.force_rebuild})..."
            )
            start_time = time.time()

            result = self.topology_service.create_topology(
                tolerance=self.tolerance,
                force_rebuild=self.force_rebuild,
            )

            elapsed = time.time() - start_time
            logger.info(f"Topology creation completed in {elapsed:.1f} seconds")

            if result and isinstance(result, dict):
                logger.info(f"Topology status: {result.get('status', 'unknown')}")
                logger.info(f"Vertices: {result.get('vertices', 0)}")
                logger.info(f"Edges: {result.get('edges', 0)}")

            self.stats["topology_created"] = True
            logger.info("Topology created successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to create topology: {e}")
            return False

    def calculate_metrics(self) -> bool:
        """Calculate all metrics."""
        if self.skip_metrics:
            logger.info("Skipping metrics calculation as requested")
            self.stats["metrics_calculated"] = True
            return True

        logger.info("Starting metrics calculation pipeline...")
        pipeline = MetricsPipeline(self.metrics_calculator)
        result = pipeline.run()

        self.stats["metrics_calculated"] = result["success"]
        self.stats["metrics_details"] = result["stats"]

        if result["errors"]:
            for error in result["errors"]:
                logger.error(f"Metrics error: {error}")

        logger.info(f"Metrics calculation success: {result['success']}")
        return result["success"]

    def run_validation(self) -> bool:
        """Run validation and reporting."""
        try:
            logger.info("Running validation...")
            reporter = ValidationReporter(
                self.topology_service, self.metrics_calculator
            )
            reporter.run()
            self.stats["validation_completed"] = True
            logger.info("Validation completed successfully")
            return True
        except Exception as e:
            logger.error(f"Validation failed: {e}")
            return False

    def run(self) -> dict:
        """Run the complete GIS preparation pipeline."""
        result = {"success": False, "errors": [], "stats": self.stats.copy()}

        try:
            logger.info("=" * 80)
            logger.info("STARTING GIS PREPARATION PIPELINE")
            logger.info(f"Area: {self.area}, Tolerance: {self.tolerance}")
            logger.info(
                f"Force rebuild: {self.force_rebuild},"
                f" Skip metrics: {self.skip_metrics}"
            )
            logger.info("=" * 80)

            total_start_time = time.time()

            # Verify extensions
            logger.info("--- STEP 1: Verifying PostgreSQL extensions ---")
            if not self.verify_extensions():
                result["errors"].append("Failed to verify/install required extensions")
                logger.error("Pipeline failed at step 1")
                return result

            # Validate data
            logger.info("--- STEP 2: Validating road data ---")
            if not self.validate_data():
                result["errors"].append("No valid road data found")
                logger.error("Pipeline failed at step 2")
                return result

            # Create topology
            logger.info("--- STEP 3: Creating routing topology ---")
            if not self.create_topology():
                result["errors"].append("Failed to create routing topology")
                logger.error("Pipeline failed at step 3")
                return result

            # Calculate metrics
            logger.info("--- STEP 4: Calculating metrics ---")
            if not self.calculate_metrics():
                result["errors"].append("Failed to calculate metrics")
                logger.error("Pipeline failed at step 4")
                return result

            # Run validation
            logger.info("--- STEP 5: Running validation ---")
            self.run_validation()

            result["success"] = True
            result["stats"] = self.stats.copy()

            total_elapsed = time.time() - total_start_time
            logger.info("=" * 80)
            logger.info("GIS PREPARATION PIPELINE COMPLETED SUCCESSFULLY")
            logger.info(
                f"Total time: {total_elapsed:.1f}"
                f" seconds ({total_elapsed / 60:.1f} minutes)"
            )
            logger.info("=" * 80)

        except Exception as e:
            logger.error(f"Pipeline error: {e}")
            result["errors"].append(f"Pipeline error: {e}")

        return result


class Command(BaseCommand):
    """Management command for preparing GIS data for scenic routing."""

    help = "Prepares real GIS database for scenic routing operations"

    def add_arguments(self, parser):
        """Define command-line arguments for data preparation."""
        parser.add_argument(
            "--area",
            type=str,
            default="test",
            help="Geographic region identifier for data context",
        )
        parser.add_argument(
            "--tolerance",
            type=float,
            default=0.00001,
            help="Spatial tolerance for topology node snapping in degrees",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force recreation of existing routing topology",
        )
        parser.add_argument(
            "--skip-metrics",
            action="store_true",
            help="Skip calculation of road metrics and scenic scores",
        )
        parser.add_argument(
            "--validate-only",
            action="store_true",
            help="Only validate existing data without processing or calculation",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show detailed output",
        )

    def setup_logging(self, verbose: bool):
        """Setup logging based on verbosity."""
        if verbose:
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            )
        else:
            logging.basicConfig(level=logging.WARNING)

    def display_header(self, options: dict):
        """Display command header."""
        self.stdout.write("=" * 80)
        self.stdout.write(f"GIS DATA PREPARATION - Area: {options['area']}")
        self.stdout.write("=" * 80)

        self.stdout.write("\nConfiguration:")
        self.stdout.write(f"  Tolerance: {options['tolerance']}")
        self.stdout.write(f"  Force rebuild: {options['force']}")
        self.stdout.write(f"  Skip metrics: {options['skip_metrics']}")
        self.stdout.write(f"  Validate only: {options['validate_only']}")

    def run_validate_only(self):
        """Run only validation without processing."""
        self.stdout.write("\nRunning validation only...")

        data_validator = RoadDataValidator()

        if data_validator.is_database_empty():
            self.stdout.write("Database is empty")
            return

        summary = data_validator.get_road_data_summary()

        self.stdout.write("\nRoad Data Summary:")
        self.stdout.write(f"  Total segments: {summary['total_segments']:,}")
        self.stdout.write(f"  With geometry: {summary['segments_with_geometry']:,}")
        self.stdout.write(
            f"  Without geometry: {summary['segments_without_geometry']:,}"
        )

        if summary["by_highway_type"]:
            self.stdout.write("\nTop 10 highway types:")
            for highway, count in summary["by_highway_type"].items():
                self.stdout.write(f"  {highway}: {count:,}")

    def display_pipeline_results(self, result: dict):
        """Display pipeline results."""
        self.stdout.write("\n" + "=" * 80)

        if result["success"]:
            self.stdout.write("PREPARATION SUCCESSFUL")

            self.stdout.write("\nSteps completed:")
            stats = result["stats"]

            if stats.get("extensions_verified"):
                self.stdout.write("  PostgreSQL extensions verified")

            if stats.get("data_valid"):
                self.stdout.write("  Road data validated")

            if stats.get("topology_created"):
                self.stdout.write("  Routing topology created")

            if stats.get("metrics_calculated"):
                self.stdout.write("  Metrics calculated")

            if stats.get("validation_completed"):
                self.stdout.write("  Validation completed")

        else:
            self.stdout.write("PREPARATION FAILED")

            if result["errors"]:
                self.stdout.write("\nErrors:")
                for error in result["errors"]:
                    self.stdout.write(f"  {error}")

        self.stdout.write("=" * 80)

    def handle(self, *args, **options):
        """Execute the GIS data preparation pipeline."""
        self.setup_logging(options["verbose"])
        self.display_header(options)

        if options["validate_only"]:
            self.run_validate_only()
            return

        data_validator = RoadDataValidator()
        if data_validator.is_database_empty():
            self.stdout.write("\nCannot proceed: No road data found")
            self.stdout.write(
                "Please run: python manage.py import_osm_roads --area test"
            )
            sys.exit(1)

        self.stdout.write("\nStarting GIS data preparation...")
        self.stdout.write(
            f"Processing {data_validator.count_road_segments():,} road segments"
        )
        self.stdout.write("This may take several minutes for large datasets...")

        pipeline = GISPreparationPipeline(
            area=options["area"],
            tolerance=options["tolerance"],
            force_rebuild=options["force"],
            skip_metrics=options["skip_metrics"],
        )

        result = pipeline.run()

        self.display_pipeline_results(result)

        if not result["success"]:
            sys.exit(1)

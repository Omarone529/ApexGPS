import contextlib
import sys

from django.core.management.base import BaseCommand
from django.db import connection

from gis_data.services.metrics_calculator import MetricsCalculator
from gis_data.services.topology_service import TopologyService


class Command(BaseCommand):
    """
    Management command for preparing real GIS data for scenic routing.

    This command orchestrates the complete data preparation pipeline:
    1. Verifies PostgreSQL spatial extensions
    2. Creates routing network topology using pgRouting
    3. Calculates physical and scenic road metrics
    4. Validates preparation results

    The command works exclusively with real imported data and does not
    generate any sample or test data.
    """

    help = "Prepares real GIS database for scenic routing operations"

    def add_arguments(self, parser):
        """
        Define command-line arguments for data preparation.

        Args:
            parser: argparse.ArgumentParser instance for argument definition
        """
        parser.add_argument(
            "--area",
            type=str,
            default="italy",
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

    def _check_extension_exists(self, cursor, extension_name):
        """Check if a PostgreSQL extension is installed."""
        cursor.execute(
            "SELECT extname, extversion FROM pg_extension WHERE extname = %s",
            [extension_name],
        )
        return cursor.fetchone()

    def _install_pgrouting_extension(self, cursor):
        """Install pgRouting extension if not present."""
        try:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pgrouting")
            return True
        except Exception as e:
            raise Exception(f"Cannot install pgrouting: {str(e)}") from e

    def _check_postgis_extensions(self):
        """Verify required PostgreSQL extensions are installed and available."""
        required_extensions = ["postgis", "pgrouting"]

        with connection.cursor() as cursor:
            for ext in required_extensions:
                extension_info = self._check_extension_exists(cursor, ext)

                if extension_info:
                    continue  # Extension exists
                elif ext == "pgrouting":
                    self._install_pgrouting_extension(cursor)
                else:
                    raise Exception(f"Extension {ext} not installed")

    def _count_road_segments(self):
        """Count road segments with valid geometry."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) FROM gis_data_roadsegment
                WHERE geometry IS NOT NULL
                """
            )
            return cursor.fetchone()[0]

    def _is_database_empty(self):
        """Check if the database contains real road data for processing."""
        road_count = self._count_road_segments()
        return road_count == 0

    def _calculate_routing_costs(self):
        """
        Calculate routing costs for different optimization strategies.

        Uses the correct formula: C = (α × length_m) - (β × scenic_score)
        where scenic_score = scenic_rating × 100

        Coefficients for different preferences:
        - Fast: α=1.0, β=0.1 (mostly distance-based)
        - Balanced: α=0.6, β=0.4 (balanced)
        - Most Winding: α=0.3, β=0.7 (heavily scenic-weighted)

        Note: cost_scenic is for "most_winding" preference
        cost_balanced is for "balanced" preference
        cost_time is for "fast" preference
        """
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE gis_data_roadsegment
                SET
                    cost_length = length_m,
                    cost_time = CASE
                        WHEN maxspeed > 0 THEN length_m / (maxspeed / 3.6)
                        ELSE length_m / (50 / 3.6)
                    END
                WHERE length_m > 0
                """
            )

            cursor.execute(
                """
                UPDATE gis_data_roadsegment
                SET cost_scenic = (0.3 * length_m) - (0.7 * (scenic_rating * 100))
                WHERE length_m > 0 AND scenic_rating IS NOT NULL
                """
            )
            cursor.execute(
                """
                UPDATE gis_data_roadsegment
                SET cost_balanced = (0.6 * length_m) - (0.4 * (scenic_rating * 100))
                WHERE length_m > 0 AND scenic_rating IS NOT NULL
                """
            )

    def _calculate_all_metrics(self, metrics_calculator):
        """Calculate all road segment metrics using the metrics calculator service."""
        metrics_calculator.calculate_core_metrics()
        metrics_calculator.calculate_scenic_scores()
        self._calculate_routing_costs()

    def _get_topology_validation(self, topology_service):
        """Get topology validation results."""
        try:
            return topology_service.validate_topology()
        except Exception:
            return None

    def _get_topology_summary(self, topology_service):
        """Get topology summary statistics."""
        try:
            return topology_service.get_topology_summary()
        except Exception:
            return None

    def _get_metrics_summary(self, metrics_calculator):
        """Get metrics summary statistics."""
        try:
            return metrics_calculator.get_metrics_summary()
        except Exception:
            return None

    def _validate_and_report(self, topology_service, metrics_calculator):
        """
        Validate preparation results and generate internal validation data.
        Performs integrity checks on the created topology and
        retrieves summary statistics for verification purposes.
        """
        self._get_topology_validation(topology_service)
        self._get_topology_summary(topology_service)
        self._get_metrics_summary(metrics_calculator)

    def _validate_existing_data(self, topology_service, metrics_calculator):
        """Validate existing GIS data without performing any processing."""
        if self._is_database_empty():
            return

        self._get_topology_summary(topology_service)
        self._get_metrics_summary(metrics_calculator)

        with contextlib.suppress(Exception):
            topology_service.validate_topology()

    def handle(self, *args, **options):
        """Execute the GIS data preparation pipeline."""
        try:
            topology_service = TopologyService()
            metrics_calculator = MetricsCalculator()

            if self._is_database_empty():
                return

            if options["validate_only"]:
                self._validate_existing_data(topology_service, metrics_calculator)
                return

            self._check_postgis_extensions()

            topology_service.create_topology(
                tolerance=options["tolerance"],
                force_rebuild=options.get("force", False),
            )

            if not options["skip_metrics"]:
                self._calculate_all_metrics(metrics_calculator)

            self._validate_and_report(topology_service, metrics_calculator)

        except Exception:
            sys.exit(1)

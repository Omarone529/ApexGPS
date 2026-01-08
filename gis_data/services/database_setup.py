import traceback

from django.conf import settings
from django.core.management import call_command
from django.db import connection


class DatabaseSetupService:
    """
    Service to check and prepare database for scenic routing operations.

    This services ensures all required data is imported and processed
    before attempting route calculations.
    """

    @staticmethod
    def check_database_status():
        """Check current status of database preparation."""
        with connection.cursor() as cursor:
            # Check if road segments table exists and has data
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'gis_data_roadsegment'
                )
            """
            )
            roads_table_exists = cursor.fetchone()[0]

            if not roads_table_exists:
                return {
                    "status": "not_ready",
                    "message": "Road segments table does not exist.",
                    "next_action": "Run migrations: python manage.py migrate",
                }

            # Check if road segments have data
            cursor.execute("SELECT COUNT(*) FROM gis_data_roadsegment")
            road_count = cursor.fetchone()[0]

            if road_count == 0:
                return {
                    "status": "not_ready",
                    "message": "No road segments found in database.",
                    "next_action": "Import roads: python manage.py import_osm_roads",
                }

            # Check if topology is created
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'gis_data_roadsegment_vertices_pgr'
                )
            """
            )
            topology_exists = cursor.fetchone()[0]

            if not topology_exists:
                return {
                    "status": "partially_ready",
                    "message": "Roads exist but topology not created.",
                    "next_action": "Prepare GIS data: "
                    "python manage.py prepare_gis_data",
                }

            # Check if costs are calculated
            cursor.execute(
                """
                SELECT COUNT(*) FROM gis_data_roadsegment
                WHERE cost_scenic IS NOT NULL AND cost_scenic != 0
            """
            )
            scenic_cost_count = cursor.fetchone()[0]

            if scenic_cost_count == 0:
                return {
                    "status": "partially_ready",
                    "message": "Topology exists but routing costs not calculated.",
                    "next_action": "Prepare GIS data: "
                    "python manage.py prepare_gis_data",
                }

            # Check if POIs exist
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'gis_data_pointofinterest'
                )
            """
            )
            pois_table_exists = cursor.fetchone()[0]

            if not pois_table_exists:
                return {
                    "status": "ready_without_pois",
                    "message": "Routing ready but no POIs imported.",
                    "next_action": "Import POIs: python manage.py import_osm_pois",
                }

            cursor.execute("SELECT COUNT(*) FROM gis_data_pointofinterest")
            poi_count = cursor.fetchone()[0]

            if poi_count == 0:
                return {
                    "status": "ready_without_pois",
                    "message": "Routing ready but POI table is empty.",
                    "next_action": "Import POIs: python manage.py import_osm_pois",
                }

            # Database is fully ready
            return {
                "status": "fully_ready",
                "message": "Database is fully prepared for routing operations.",
                "road_segments": road_count,
                "pois": poi_count,
                "scenic_costs_calculated": scenic_cost_count,
            }

    @staticmethod
    def run_full_setup(area="italy", force=False):
        """Run complete database setup from scratch."""
        results = {"steps_completed": [], "errors": [], "status": "pending"}

        try:
            # Check if roads exist or need import
            status = DatabaseSetupService.check_database_status()

            if status["status"] == "not_ready":
                # Import roads
                results["steps_completed"].append("Starting road import...")
                clear_flag = "--clear" if force else ""
                call_command("import_osm_roads", "--area", area, clear_flag)
                results["steps_completed"].append("Road import completed")

            # Import POIs if needed
            needs_pois = status["status"] in [
                "ready_without_pois",
                "not_ready",
                "partially_ready",
            ]

            if needs_pois:
                results["steps_completed"].append("Starting POI import...")
                clear_flag = "--clear" if force else ""
                call_command(
                    "import_osm_pois", "--area", area, clear_flag, "--categories", "all"
                )
                results["steps_completed"].append("POI import completed")

            # Prepare GIS data (topology + costs)
            results["steps_completed"].append("Preparing GIS data...")
            force_flag = "--force" if force else ""
            call_command("prepare_gis_data", "--area", area, force_flag)
            results["steps_completed"].append("GIS data preparation completed")

            # Final status check
            final_status = DatabaseSetupService.check_database_status()
            results["final_status"] = final_status
            results["status"] = "completed"

        except Exception as e:
            results["status"] = "failed"
            results["errors"].append(str(e))
            results["traceback"] = traceback.format_exc()

        return results

    @staticmethod
    def ensure_database_ready():
        """
        Ensure database is ready for routing operations.
        If not ready, attempt automatic setup.
        """
        status = DatabaseSetupService.check_database_status()

        if status["status"] == "fully_ready":
            return True

        # Attempt automatic setup
        print(f"Database not ready: {status['message']}")
        print("Attempting automatic setup...")

        result = DatabaseSetupService.run_full_setup()

        if result["status"] == "completed":
            print("Database setup completed successfully!")
            return True
        else:
            error_msg = result.get("errors", ["Unknown error"])
            print(f"Database setup failed: {error_msg}")
            return False


def check_database_middleware(get_response):
    """
    Django middleware to check database readiness on each request.
    Only active in DEBUG mode.
    """

    def middleware(request):
        if settings.DEBUG and not hasattr(request, "_database_checked"):
            DatabaseSetupService.ensure_database_ready()
            request._database_checked = True

        return get_response(request)

    return middleware

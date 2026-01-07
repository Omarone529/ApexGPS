import logging

from django.db import connection, transaction

logger = logging.getLogger(__name__)

__all__ = ["TopologyService"]


class TopologyService:
    """
    Service class for pgRouting topology operations.
    Handles the creation and management of directed graph topology
    from road segment geometries for efficient route calculation.
    """

    @staticmethod
    def _check_topology_columns_exist():
        """Check if topology columns (source, target) exist in roadsegment table."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'gis_data_roadsegment'
                AND column_name IN ('source', 'target')
            """
            )
            return {row[0] for row in cursor.fetchall()}

    @staticmethod
    def _add_missing_topology_columns(existing_columns):
        """Add source and target columns if they don't exist."""
        with connection.cursor() as cursor:
            if "source" not in existing_columns:
                cursor.execute(
                    "ALTER TABLE gis_data_roadsegment ADD COLUMN source INTEGER"
                )
            if "target" not in existing_columns:
                cursor.execute(
                    "ALTER TABLE gis_data_roadsegment ADD COLUMN target INTEGER"
                )

    @staticmethod
    def _ensure_geometry_index_exists():
        """Create spatial index on geometry column if it doesn't exist."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) FROM pg_indexes
                WHERE tablename = 'gis_data_roadsegment'
                AND indexdef LIKE '%geometry%'
            """
            )
            if cursor.fetchone()[0] == 0:
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS roadsegment_geometry_idx
                    ON gis_data_roadsegment USING GIST (geometry)
                """
                )

    @staticmethod
    def _execute_topology_creation(tolerance):
        """Execute pgRouting topology creation function with correct parameters."""
        with connection.cursor() as cursor:
            try:
                # First, try the modern syntax (pgRouting 3.0+)
                cursor.execute(
                    f"""
                    SELECT pgr_createTopology(
                        'gis_data_roadsegment',
                        {tolerance},
                        'geometry',
                        'id',
                        'source',
                        'target'
                    )
                """
                )
                return cursor.fetchone()[0]
            except Exception as e:
                logger.error(f"Modern pgr_createTopology failed: {e}")
                # Fallback to simpler syntax
                try:
                    cursor.execute(
                        f"""
                        SELECT pgr_createTopology(
                            'gis_data_roadsegment',
                            {tolerance},
                            'geometry',
                            'id'
                        )
                    """
                    )
                    return cursor.fetchone()[0]
                except Exception as e2:
                    logger.error(f"Simple pgr_createTopology also failed: {e2}")
                    # Last resort - direct SQL
                    cursor.execute(
                        f"""
                        SELECT _pgr_createTopology(
                            'gis_data_roadsegment',
                            {tolerance},
                            'geometry',
                            'id',
                            'source',
                            'target',
                            rows_where := 'geometry IS NOT NULL',
                            clean := true
                        )
                    """
                    )
                    return cursor.fetchone()[0]

    @staticmethod
    def _get_topology_metrics():
        """Get metrics about created topology (vertices and edges)."""
        with connection.cursor() as cursor:
            try:
                cursor.execute("SELECT COUNT(*) FROM gis_data_roadsegment_vertices_pgr")
                vertices = cursor.fetchone()[0]
            except Exception:
                vertices = 0

            cursor.execute(
                """
                SELECT COUNT(*) FROM gis_data_roadsegment
                WHERE source IS NOT NULL AND target IS NOT NULL
            """
            )
            edges = cursor.fetchone()[0]

            return vertices, edges

    @staticmethod
    def _check_vertices_table_exists():
        """Check if pgRouting vertices table exists."""
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

    @staticmethod
    @transaction.atomic
    def create_topology(tolerance=0.00001, force_rebuild=False):
        """Create or update routing network topology."""
        # Check if vertices table already exists
        vertices_table_exists = TopologyService._check_vertices_table_exists()

        existing_columns = TopologyService._check_topology_columns_exist()
        has_topology = {"source", "target"}.issubset(existing_columns)

        # If vertices table exists and system is not rebuilding, skip
        if vertices_table_exists and has_topology and not force_rebuild:
            logger.info("Topology already exists, skipping creation")
            vertices, edges = TopologyService._get_topology_metrics()
            return {
                "status": "exists",
                "action": "skipped",
                "vertices": vertices,
                "edges": edges,
            }

        # If forcing rebuild, drop existing topology tables
        if force_rebuild:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DROP TABLE IF EXISTS gis_data_roadsegment_vertices_pgr CASCADE;"
                )
                cursor.execute(
                    "UPDATE gis_data_roadsegment SET source = NULL, target = NULL;"
                )

        # Ensure required columns and index
        TopologyService._add_missing_topology_columns(existing_columns)
        TopologyService._ensure_geometry_index_exists()

        # Create topology
        topology_result = TopologyService._execute_topology_creation(tolerance)
        vertices, edges = TopologyService._get_topology_metrics()

        return {
            "status": "created",
            "result": topology_result,
            "vertices": vertices,
            "edges": edges,
            "tolerance": tolerance,
        }

    @staticmethod
    def _count_disconnected_segments():
        """Count road segments without topology connections."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) as disconnected
                FROM gis_data_roadsegment
                WHERE geometry IS NOT NULL
                AND (source IS NULL OR target IS NULL)
            """
            )
            return cursor.fetchone()[0]

    @staticmethod
    def _count_isolated_vertices():
        """Count vertices not connected to any edges."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) as isolated
                FROM gis_data_roadsegment_vertices_pgr v
                WHERE NOT EXISTS (
                    SELECT 1 FROM gis_data_roadsegment
                    WHERE source = v.id OR target = v.id
                )
            """
            )
            return cursor.fetchone()[0]

    @staticmethod
    def _find_duplicate_edges():
        """Find duplicate edges (same source-target pairs)."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT source, target, COUNT(*) as duplicate_count
                FROM gis_data_roadsegment
                WHERE source IS NOT NULL AND target IS NOT NULL
                GROUP BY source, target
                HAVING COUNT(*) > 1
            """
            )
            return cursor.fetchall()

    @staticmethod
    def validate_topology():
        """
        Validate routing topology integrity.

        Checks for disconnected segments, isolated vertices, and duplicate edges
        to ensure the routing network is properly connected.
        """
        vertices_table_exists = TopologyService._check_vertices_table_exists()

        if not vertices_table_exists:
            return {
                "disconnected_segments": 0,
                "isolated_vertices": 0,
                "duplicate_edges": 0,
                "vertices_table_exists": False,
                "is_valid": False,
            }

        disconnected = TopologyService._count_disconnected_segments()
        isolated = TopologyService._count_isolated_vertices()
        duplicates = TopologyService._find_duplicate_edges()

        return {
            "disconnected_segments": disconnected,
            "isolated_vertices": isolated,
            "duplicate_edges": len(duplicates),
            "vertices_table_exists": True,
            "is_valid": disconnected == 0 and isolated == 0 and len(duplicates) == 0,
        }

    @staticmethod
    def _get_road_segment_statistics():
        """Get basic statistics about road segments."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) as total_segments,
                    COUNT(DISTINCT source) as unique_sources,
                    COUNT(DISTINCT target) as unique_targets,
                    COUNT(CASE WHEN source IS NOT NULL AND target IS NOT NULL
                          THEN 1 END) as routable_segments,
                    ROUND(AVG(length_m)::numeric, 1) as avg_segment_length
                FROM gis_data_roadsegment
            """
            )
            return cursor.fetchone()

    @staticmethod
    def _count_total_vertices():
        """Count total vertices in routing graph."""
        with connection.cursor() as cursor:
            try:
                cursor.execute("SELECT COUNT(*) FROM gis_data_roadsegment_vertices_pgr")
                return cursor.fetchone()[0]
            except Exception:
                return 0

    @staticmethod
    def get_topology_summary():
        """Get comprehensive summary statistics of routing topology."""
        stats = TopologyService._get_road_segment_statistics()
        total_vertices = TopologyService._count_total_vertices()

        return {
            "total_segments": stats[0],
            "routable_segments": stats[3],
            "total_vertices": total_vertices,
            "unique_sources": stats[1],
            "unique_targets": stats[2],
            "avg_segment_length_m": stats[4] or 0,
        }

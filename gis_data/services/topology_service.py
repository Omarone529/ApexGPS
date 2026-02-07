import logging
from django.db import connection

logger = logging.getLogger(__name__)

__all__ = ["TopologyService"]

class TopologyService:
    """
    Service for creating and managing pgRouting v4.0+ topology.

    This service handles the creation of a directed graph topology from road
    segment geometries using pgRouting v4.0's pgr_extractVertices function.
    It replaces the deprecated pgr_createTopology method and provides
    additional validation, summary, and cleanup capabilities.

    Features:
    - Creates vertices table using pgr_extractVertices
    - Updates source/target columns for routing
    - Calculates routing costs (time, scenic, balanced)
    - Validates topology integrity
    - Provides detailed topology statistics
    - Cleans up invalid topology data

    Usage:
        service = TopologyService()
        result = service.create_topology(tolerance=0.00001)
        summary = service.get_topology_summary()
    """

    def __init__(self):
        self.table_name = 'gis_data_roadsegment'
        self.vertices_table = f'{self.table_name}_vertices_pgr'

    def create_topology(self, tolerance=0.00001, force_rebuild=False):
        """
        Create topology using pgRouting v4.0+ method
        """
        try:
            logger.info(f"Creating topology with pgRouting v4.0+ (tolerance: {tolerance})")

            with connection.cursor() as cursor:
                # Drop existing vertices if force rebuild
                if force_rebuild:
                    cursor.execute(f"DROP TABLE IF EXISTS {self.vertices_table} CASCADE;")

                # Step 1: Create vertices table using pgr_extractVertices
                logger.info("Step 1: Creating vertices table with pgr_extractVertices...")
                cursor.execute(f"""
                    -- Create vertices table using pgRouting v4.0+ function
                    SELECT * INTO {self.vertices_table}
                    FROM pgr_extractVertices(
                        'SELECT id, geometry as geom 
                         FROM {self.table_name} 
                         WHERE geometry IS NOT NULL 
                         ORDER BY id'
                    );
                """)

                cursor.execute(f"""
                    ALTER TABLE {self.vertices_table} 
                    ALTER COLUMN geom TYPE geometry(Point, 4326)
                    USING ST_SetSRID(geom, 4326);
                """)

                cursor.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{self.vertices_table}_geom 
                    ON {self.vertices_table} USING GIST(geom);
                """)

                # Step 2: Update source column (start points)
                logger.info("Step 2: Updating source column...")
                cursor.execute(f"""
                    UPDATE {self.table_name} AS e
                    SET source = v.id, 
                        x1 = v.x, 
                        y1 = v.y
                    FROM {self.vertices_table} AS v
                    WHERE ST_StartPoint(e.geometry) = v.geom
                    AND e.geometry IS NOT NULL;
                """)
                source_updated = cursor.rowcount
                logger.info(f"Updated source for {source_updated} segments")

                # Step 3: Update target column (end points)
                logger.info("Step 3: Updating target column...")
                cursor.execute(f"""
                    UPDATE {self.table_name} AS e
                    SET target = v.id, 
                        x2 = v.x, 
                        y2 = v.y
                    FROM {self.vertices_table} AS v
                    WHERE ST_EndPoint(e.geometry) = v.geom
                    AND e.geometry IS NOT NULL;
                """)
                target_updated = cursor.rowcount
                logger.info(f"Updated target for {target_updated} segments")

                # Step 4: Update cost columns if they're NULL
                logger.info("Step 4: Updating routing costs...")
                cursor.execute(f"""
                    -- Update cost_length with actual length
                    UPDATE {self.table_name}
                    SET cost_length = ST_Length(geometry::geography)
                    WHERE cost_length = 0 AND geometry IS NOT NULL;

                    -- Update cost_time based on maxspeed
                    UPDATE {self.table_name}
                    SET cost_time = 
                        CASE
                            WHEN maxspeed > 0 THEN 
                                ST_Length(geometry::geography) / (maxspeed / 3.6)
                            ELSE 
                                ST_Length(geometry::geography) / (50 / 3.6)  -- Default 50 km/h
                        END
                    WHERE cost_time = 0 AND geometry IS NOT NULL;

                    -- Update scenic costs
                    UPDATE {self.table_name}
                    SET cost_scenic = 
                        (0.3 * ST_Length(geometry::geography)) - 
                        (0.7 * COALESCE(scenic_rating, 5.0) * 100)
                    WHERE geometry IS NOT NULL;

                    -- Update balanced costs
                    UPDATE {self.table_name}
                    SET cost_balanced = 
                        (0.6 * ST_Length(geometry::geography) / 1000) - 
                        (0.4 * COALESCE(scenic_rating, 5.0) / 10)
                    WHERE geometry IS NOT NULL;
                """)

                # Step 5: Create indexes for performance
                logger.info("Step 5: Creating indexes...")
                cursor.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{self.table_name}_source 
                    ON {self.table_name}(source);

                    CREATE INDEX IF NOT EXISTS idx_{self.table_name}_target 
                    ON {self.table_name}(target);

                    CREATE INDEX IF NOT EXISTS idx_{self.table_name}_source_target 
                    ON {self.table_name}(source, target);

                    CREATE INDEX IF NOT EXISTS idx_{self.table_name}_geometry 
                    ON {self.table_name} USING GIST(geometry);
                """)

                # Get statistics
                cursor.execute(f"SELECT COUNT(*) FROM {self.vertices_table}")
                vertices = cursor.fetchone()[0]

                cursor.execute(f"""
                    SELECT COUNT(*) FROM {self.table_name}
                    WHERE source IS NOT NULL AND target IS NOT NULL
                    AND geometry IS NOT NULL
                """)
                edges = cursor.fetchone()[0]

                cursor.execute(f"""
                    SELECT COUNT(*) FROM {self.table_name}
                    WHERE geometry IS NOT NULL
                """)
                total_segments = cursor.fetchone()[0]

                coverage = (edges / total_segments * 100) if total_segments > 0 else 0

                logger.info(f"Topology created successfully!")
                logger.info(f"  Vertices: {vertices}")
                logger.info(f"  Routable edges: {edges}")
                logger.info(f"  Total segments: {total_segments}")
                logger.info(f"  Coverage: {coverage:.1f}%")

                return {
                    'status': 'success',
                    'vertices': vertices,
                    'edges': edges,
                    'total_segments': total_segments,
                    'coverage_percentage': coverage,
                    'source_updated': source_updated,
                    'target_updated': target_updated,
                    'method': 'pgr_extractVertices (v4.0)',
                    'success': True
                }

        except Exception as e:
            logger.error(f"Failed to create topology: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    def validate_topology(self):
        """
        Validate topology integrity by checking for:
        - Missing vertices table
        - Disconnected road segments
        - Isolated vertices (not connected to any edges)
        - Self-loops (source == target)

        Returns:
            dict: Validation results with boolean 'is_valid' flag
                and detailed error counts
        """
        try:
            with connection.cursor() as cursor:
                # Check if vertices table exists
                cursor.execute(f"""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables 
                        WHERE table_name = '{self.vertices_table}'
                    );
                """)
                vertices_exists = cursor.fetchone()[0]

                if not vertices_exists:
                    return {
                        'is_valid': False,
                        'vertices_table_exists': False,
                        'error': 'Vertices table does not exist'
                    }

                # Count disconnected segments
                cursor.execute(f"""
                    SELECT COUNT(*) as disconnected
                    FROM {self.table_name}
                    WHERE geometry IS NOT NULL
                    AND (source IS NULL OR target IS NULL);
                """)
                disconnected = cursor.fetchone()[0]

                # Count isolated vertices
                cursor.execute(f"""
                    SELECT COUNT(*) as isolated
                    FROM {self.vertices_table} v
                    WHERE NOT EXISTS (
                        SELECT 1 FROM {self.table_name}
                        WHERE source = v.id OR target = v.id
                    );
                """)
                isolated = cursor.fetchone()[0]

                # Check for self-loops
                cursor.execute(f"""
                    SELECT COUNT(*) as self_loops
                    FROM {self.table_name}
                    WHERE source = target
                    AND source IS NOT NULL;
                """)
                self_loops = cursor.fetchone()[0]

                is_valid = (disconnected == 0 and isolated == 0 and self_loops == 0)

                return {
                    'is_valid': is_valid,
                    'vertices_table_exists': True,
                    'disconnected_segments': disconnected,
                    'isolated_vertices': isolated,
                    'self_loops': self_loops,
                    'validation_passed': is_valid
                }

        except Exception as e:
            logger.error(f"Topology validation failed: {e}")
            return {'is_valid': False, 'error': str(e)}

    def get_topology_summary(self):
        """
        Generate comprehensive topology statistics including:
        - Total vs routable segments
        - Vertex and edge counts
        - Segment length statistics
        - Road type distribution
        - Routing cost averages

        Returns:
            dict: Detailed topology metrics for monitoring
                and optimization purposes
        """
        try:
            with connection.cursor() as cursor:
                # Basic segment statistics
                cursor.execute(f"""
                    SELECT
                        COUNT(*) as total_segments,
                        COUNT(DISTINCT source) as unique_sources,
                        COUNT(DISTINCT target) as unique_targets,
                        COUNT(CASE WHEN source IS NOT NULL AND target IS NOT NULL
                              THEN 1 END) as routable_segments,
                        ROUND(AVG(ST_Length(geometry::geography))::numeric, 1) as avg_length_m,
                        ROUND(MIN(ST_Length(geometry::geography))::numeric, 1) as min_length_m,
                        ROUND(MAX(ST_Length(geometry::geography))::numeric, 1) as max_length_m,
                        SUM(ST_Length(geometry::geography)) / 1000 as total_length_km
                    FROM {self.table_name}
                    WHERE geometry IS NOT NULL;
                """)
                seg_stats = cursor.fetchone()

                # Vertex count
                cursor.execute(f"SELECT COUNT(*) FROM {self.vertices_table}")
                total_vertices = cursor.fetchone()[0]

                # Road type distribution
                cursor.execute(f"""
                    SELECT 
                        COALESCE(highway, 'unknown') as road_type,
                        COUNT(*) as count,
                        ROUND(SUM(ST_Length(geometry::geography)) / 1000, 1) as length_km
                    FROM {self.table_name}
                    WHERE geometry IS NOT NULL
                    GROUP BY highway
                    ORDER BY count DESC
                    LIMIT 10;
                """)
                road_types = cursor.fetchall()

                # Cost statistics
                cursor.execute(f"""
                    SELECT
                        ROUND(AVG(cost_time)::numeric, 1) as avg_time_cost,
                        ROUND(AVG(cost_scenic)::numeric, 1) as avg_scenic_cost,
                        ROUND(AVG(scenic_rating)::numeric, 2) as avg_scenic_rating
                    FROM {self.table_name}
                    WHERE geometry IS NOT NULL;
                """)
                cost_stats = cursor.fetchone()

                return {
                    'total_segments': seg_stats[0] or 0,
                    'routable_segments': seg_stats[3] or 0,
                    'total_vertices': total_vertices,
                    'unique_sources': seg_stats[1] or 0,
                    'unique_targets': seg_stats[2] or 0,
                    'avg_segment_length_m': seg_stats[4] or 0,
                    'min_segment_length_m': seg_stats[5] or 0,
                    'max_segment_length_m': seg_stats[6] or 0,
                    'total_length_km': float(seg_stats[7] or 0),
                    'routable_percentage': (seg_stats[3] / seg_stats[0] * 100) if seg_stats[0] > 0 else 0,
                    'avg_time_cost': cost_stats[0] or 0,
                    'avg_scenic_cost': cost_stats[1] or 0,
                    'avg_scenic_rating': cost_stats[2] or 0,
                    'road_types': [
                        {
                            'type': rt[0],
                            'count': rt[1],
                            'length_km': float(rt[2])
                        }
                        for rt in road_types
                    ]
                }

        except Exception as e:
            logger.error(f"Failed to get topology summary: {e}")
            return {
                'total_segments': 0,
                'routable_segments': 0,
                'total_vertices': 0,
                'error': str(e)
            }

    def cleanup_topology(self):
        """
        Clean invalid topology data by:
        - Removing segments with NULL geometry
        - Resetting source/target for self-loops
        - Deleting isolated vertices

        Returns:
            dict: Cleanup results with count of removed entries
        """
        try:
            with connection.cursor() as cursor:
                # Remove segments with NULL geometry
                cursor.execute(f"""
                    DELETE FROM {self.table_name}
                    WHERE geometry IS NULL;

                    -- Reset source/target for invalid segments
                    UPDATE {self.table_name}
                    SET source = NULL, target = NULL
                    WHERE source IS NOT NULL 
                    AND target IS NOT NULL
                    AND source = target;

                    -- Remove isolated vertices
                    DELETE FROM {self.vertices_table} v
                    WHERE NOT EXISTS (
                        SELECT 1 FROM {self.table_name}
                        WHERE source = v.id OR target = v.id
                    );
                """)

                cleaned = cursor.rowcount
                logger.info(f"Cleaned {cleaned} invalid topology entries")

                return {'success': True, 'cleaned_count': cleaned}

        except Exception as e:
            logger.error(f"Cleanup failed: {e}")
            return {'success': False, 'error': str(e)}
from django.db import connection
import time
import logging

logger = logging.getLogger(__name__)

__all__ = ["MetricsCalculator"]


class MetricsCalculator:
    """
    Service class for road metric calculations.

    Handles the computation of both physical road characteristics
    and scenic quality scores used in panoramic route optimization.
    """

    @staticmethod
    def _update_road_lengths():
        """Calculate and update accurate geographic lengths."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE gis_data_roadsegment
                SET length_m = ST_Length(geometry::geography)
                WHERE geometry IS NOT NULL
                AND (length_m = 0 OR length_m IS NULL)
            """
            )
            return cursor.rowcount

    @staticmethod
    def _update_curvature():
        """Calculate and update curvature (sinuosity) values."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE gis_data_roadsegment
                SET curvature = COALESCE(
                    CASE
                        WHEN ST_Length(geometry::geography) > 0
                        THEN ST_Length(geometry::geography) /
                             NULLIF(
                                 ST_Distance(
                                     ST_StartPoint(geometry)::geography,
                                     ST_EndPoint(geometry)::geography
                                 ),
                                 0
                             )
                        ELSE 1.0
                    END,
                    1.0  -- Default value when NULL
                )
                WHERE geometry IS NOT NULL
            """
            )
            return cursor.rowcount

    @staticmethod
    def _update_travel_times():
        """Calculate and update estimated travel times."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE gis_data_roadsegment
                SET cost_time = CASE
                    WHEN maxspeed > 0 THEN length_m / (maxspeed / 3.6)
                    ELSE length_m / (50 / 3.6)
                END
                WHERE length_m > 0
                AND (cost_time = 0 OR cost_time IS NULL)
            """
            )
            return cursor.rowcount

    @staticmethod
    def _get_core_metric_statistics():
        """Get average values for core metrics."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    ROUND(AVG(length_m)::numeric, 1) as avg_length,
                    ROUND(AVG(curvature)::numeric, 3) as avg_curvature,
                    ROUND(AVG(cost_time)::numeric, 1) as avg_travel_time_sec
                FROM gis_data_roadsegment
                WHERE length_m > 0
            """
            )
            return cursor.fetchone()

    @staticmethod
    def calculate_core_metrics():
        """
        Calculate core physical metrics for road segments based on:
        - Accurate geographic length using PostGIS geography functions.
        - Curvature (sinuosity) ratio comparing actual vs straight-line distance.
        - Estimated travel time based on road classification and speed limits.
        """
        results = {}

        results["lengths_calculated"] = MetricsCalculator._update_road_lengths()
        results["curvature_calculated"] = MetricsCalculator._update_curvature()
        results["travel_times_calculated"] = MetricsCalculator._update_travel_times()

        stats = MetricsCalculator._get_core_metric_statistics()
        results["average_length_m"] = stats[0] or 0
        results["average_curvature"] = stats[1] or 1.0
        results["average_travel_time_sec"] = stats[2] or 0

        return results

    #TODO
    @staticmethod
    def _update_poi_density():
        """Calculate and update POI density within 1km buffer."""

        # Process 5000 segments at a time
        batch_size = 5000
        total_updated = 0

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM gis_data_roadsegment
                WHERE geometry IS NOT NULL
                AND (poi_density = 0 OR poi_density IS NULL)
            """
            )
            total_to_process = cursor.fetchone()[0]

            if total_to_process == 0:
                logger.info("No segments need POI density calculation")
                return 0

            logger.info(
                f"Starting POI density calculation for {total_to_process:,}"
                f" segments (batch size: {batch_size})"
            )

            # Get approximate segments per minute
            start_time = time.time()

            # Process in batches
            for offset in range(0, total_to_process, batch_size):
                batch_start = time.time()

                cursor.execute(
                    f"""
                    -- Process one batch of segments
                    WITH batch_segments AS (
                        SELECT id, geometry, length_m
                        FROM gis_data_roadsegment
                        WHERE geometry IS NOT NULL
                        AND (poi_density = 0 OR poi_density IS NULL)
                        ORDER BY id
                        LIMIT {batch_size} OFFSET {offset}
                    ),
                    segment_densities AS (
                        SELECT
                            bs.id,
                            COUNT(poi.id)::float / GREATEST(bs.length_m, 1000.0)
                            as density
                        FROM batch_segments bs
                        LEFT JOIN gis_data_pointofinterest poi ON ST_DWithin(
                            bs.geometry::geography,
                            poi.location::geography,
                            1000
                        )
                        GROUP BY bs.id, bs.length_m
                    )
                    UPDATE gis_data_roadsegment rs
                    SET poi_density = sd.density
                    FROM segment_densities sd
                    WHERE rs.id = sd.id
                """
                )

                batch_updated = cursor.rowcount
                total_updated += batch_updated

                batch_elapsed = time.time() - batch_start
                current_progress = min(offset + batch_size, total_to_process)
                percent_complete = (current_progress / total_to_process) * 100

                # Calculate ETA (estimated time arrival)
                elapsed_total = time.time() - start_time
                if percent_complete > 0:
                    estimated_total_time = (elapsed_total / percent_complete) * 100
                    eta_seconds = estimated_total_time - elapsed_total
                    eta_minutes = eta_seconds / 60
                else:
                    eta_minutes = 0

                logger.info(
                    f"POI density: Processed {current_progress:,}/{total_to_process:,} "
                    f"segments "
                    f"({percent_complete:.1f}%) "
                    f"- Batch: {batch_elapsed:.1f}s "
                    f"- ETA: {eta_minutes:.1f} min"
                )

                # Small pause to prevent DB overload
                if offset + batch_size < total_to_process and batch_elapsed < 10:
                    time.sleep(0.2)

            total_elapsed = time.time() - start_time
            logger.info(
                f"POI density calculation complete: {total_updated:,} "
                f"segments updated in {total_elapsed:.1f}s"
            )
            return total_updated

    #TODO
    @staticmethod
    def _update_weighted_poi_density():
        """Calculate and update weighted POI density - BATCH VERSION."""
        batch_size = 5000  # Process 5000 segments at a time
        total_updated = 0

        with connection.cursor() as cursor:
            # Get total segments to process
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM gis_data_roadsegment
                WHERE geometry IS NOT NULL
                AND (weighted_poi_density = 0 OR weighted_poi_density IS NULL)
            """
            )
            total_to_process = cursor.fetchone()[0]

            if total_to_process == 0:
                logger.info("No segments need weighted POI density calculation")
                return 0

            logger.info(
                f"Starting weighted POI density calculation for {total_to_process:,}"
                f" segments"
            )

            # Get approximate segments per minute for ETA
            start_time = time.time()

            # Process in batches
            for offset in range(0, total_to_process, batch_size):
                batch_start = time.time()

                cursor.execute(
                    f"""
                    -- Process one batch of segments
                    WITH batch_segments AS (
                        SELECT id, geometry, length_m
                        FROM gis_data_roadsegment
                        WHERE geometry IS NOT NULL
                        AND (weighted_poi_density = 0 OR weighted_poi_density IS NULL)
                        ORDER BY id
                        LIMIT {batch_size} OFFSET {offset}
                    ),
                    segment_weighted_densities AS (
                        SELECT
                            bs.id,
                            COALESCE(SUM(poi.importance_score), 0) /
                            GREATEST(bs.length_m, 1000.0) as weighted_density
                        FROM batch_segments bs
                        LEFT JOIN gis_data_pointofinterest poi ON ST_DWithin(
                            bs.geometry::geography,
                            poi.location::geography,
                            1000
                        )
                        GROUP BY bs.id, bs.length_m
                    )
                    UPDATE gis_data_roadsegment rs
                    SET weighted_poi_density = swd.weighted_density
                    FROM segment_weighted_densities swd
                    WHERE rs.id = swd.id
                """
                )

                batch_updated = cursor.rowcount
                total_updated += batch_updated

                batch_elapsed = time.time() - batch_start
                current_progress = min(offset + batch_size, total_to_process)
                percent_complete = (current_progress / total_to_process) * 100

                # Calculate ETA
                elapsed_total = time.time() - start_time
                if percent_complete > 0:
                    estimated_total_time = (elapsed_total / percent_complete) * 100
                    eta_seconds = estimated_total_time - elapsed_total
                    eta_minutes = eta_seconds / 60
                else:
                    eta_minutes = 0

                logger.info(
                    f"Weighted POI density: Processed {current_progress:,}/"
                    f"{total_to_process:,} segments "
                    f"({percent_complete:.1f}%) "
                    f"- Batch: {batch_elapsed:.1f}s "
                    f"- ETA: {eta_minutes:.1f} min"
                )

                # Small pause to prevent DB overload
                if offset + batch_size < total_to_process and batch_elapsed < 10:
                    time.sleep(0.2)

            total_elapsed = time.time() - start_time
            logger.info(
                f"Weighted POI density calculation complete: {total_updated:,} "
                f"segments updated in {total_elapsed:.1f}s"
            )
            return total_updated

    #TODO
    @staticmethod
    def _assign_base_scenic_ratings():
        """Assign initial scenic ratings based on road classification."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE gis_data_roadsegment
                SET scenic_rating = CASE
                    WHEN highway IN ('motorway', 'trunk', 'primary') THEN 3.0
                    WHEN highway IN ('secondary', 'tertiary') THEN 5.0
                    WHEN highway IN ('unclassified', 'residential') THEN 4.0
                    WHEN highway IN ('track', 'path', 'footway') THEN 7.0
                    ELSE 5.0
                END
                WHERE scenic_rating = 0 OR scenic_rating IS NULL
            """
            )
            updated = cursor.rowcount
            logger.info(f"Base scenic ratings assigned to {updated:,} segments")
            return updated

    #TODO
    @staticmethod
    def _enhance_scenic_with_poi_density():
        """Enhance scenic ratings with POI density bonus."""
        batch_size = 10000
        total_updated = 0

        with connection.cursor() as cursor:
            # Get segments with POI density
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM gis_data_roadsegment
                WHERE poi_density > 0
                AND scenic_rating > 0
            """
            )
            total_to_process = cursor.fetchone()[0]

            if total_to_process == 0:
                logger.info("No segments need scenic rating enhancement")
                return 0

            logger.info(
                f"Enhancing scenic ratings for {total_to_process:,}"
                f" segments with POI density"
            )

            # Get approximate segments per minute for ETA
            start_time = time.time()

            # Process in batches
            for offset in range(0, total_to_process, batch_size):
                batch_start = time.time()

                cursor.execute(
                    f"""
                    UPDATE gis_data_roadsegment
                    SET scenic_rating = LEAST(10.0,
                        scenic_rating + (poi_density * 0.5)
                    )
                    WHERE id IN (
                        SELECT id
                        FROM gis_data_roadsegment
                        WHERE poi_density > 0
                        AND scenic_rating > 0
                        ORDER BY id
                        LIMIT {batch_size} OFFSET {offset}
                    )
                """
                )

                batch_updated = cursor.rowcount
                total_updated += batch_updated

                batch_elapsed = time.time() - batch_start
                current_progress = min(offset + batch_size, total_to_process)
                percent_complete = (current_progress / total_to_process) * 100
                elapsed_total = time.time() - start_time
                if percent_complete > 0:
                    estimated_total_time = (elapsed_total / percent_complete) * 100
                    eta_seconds = estimated_total_time - elapsed_total
                    eta_minutes = eta_seconds / 60
                else:
                    eta_minutes = 0

                logger.info(
                    f"Scenic enhancement: Processed {current_progress:,}/"
                    f"{total_to_process:,} segments "
                    f"({percent_complete:.1f}%) "
                    f"- Batch: {batch_elapsed:.1f}s "
                    f"- ETA: {eta_minutes:.1f} min"
                )

            total_elapsed = time.time() - start_time
            logger.info(
                f"Scenic rating enhancement complete: {total_updated:,}"
                f" segments updated in {total_elapsed:.1f}s"
            )
            return total_updated

    #TODO
    @staticmethod
    def _get_scenic_statistics():
        """Get statistics about scenic ratings and POI density."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    ROUND(AVG(scenic_rating)::numeric, 2) as avg_scenic,
                    ROUND(AVG(poi_density)::numeric, 3) as avg_poi_density,
                    COUNT(CASE WHEN scenic_rating >= 8 THEN 1 END)
                    as highly_scenic_count,
                    COUNT(CASE WHEN scenic_rating <= 3 THEN 1 END) as low_scenic_count
                FROM gis_data_roadsegment
                WHERE scenic_rating > 0
            """
            )
            return cursor.fetchone()

    @staticmethod
    def calculate_scenic_scores(batch_size=10000):
        """
        Calculate and update scenic scores for road segments in batched database operations.

        This method processes road segments in configurable batches to compute scenic ratings
        based on surrounding Points of Interest (POIs) density and road classification.
        It performs database updates with batch-optimized queries to improve performance
        and memory efficiency when processing large datasets.

        The scenic score calculation includes:
        1. Base rating from road type classification (highway category)
        2. Density adjustment based on POI count per kilometer
        3. Weighted POI density using importance scores

        """
        results = {}
        overall_start_time = time.time()

        logger.info(f"Starting batch-optimized scenic score calculation (batch size: {batch_size})...")

        total_updated = 0

        with connection.cursor() as cursor:
            # Step 1: Get total segments to process
            cursor.execute("""
                SELECT COUNT(*) 
                FROM gis_data_roadsegment 
                WHERE geometry IS NOT NULL
            """)
            total_segments = cursor.fetchone()[0]

            logger.info(f"Processing {total_segments:,} total segments")

            # Process in batches
            for offset in range(0, total_segments, batch_size):
                batch_start = time.time()

                try:
                    # Process each batch with comprehensive update
                    cursor.execute(f"""
                        -- Update POI densities and scenic ratings for one batch
                        WITH batch_segments AS (
                            SELECT id, geometry, length_m, highway
                            FROM gis_data_roadsegment
                            WHERE geometry IS NOT NULL
                            ORDER BY id
                            LIMIT {batch_size} OFFSET {offset}
                        ),
                        segment_poi_counts AS (
                            SELECT 
                                bs.id,
                                COUNT(poi.id)::float as poi_count,
                                COALESCE(SUM(poi.importance_score), 0)::float as weighted_poi_sum
                            FROM batch_segments bs
                            LEFT JOIN gis_data_pointofinterest poi ON 
                                ST_DWithin(
                                    bs.geometry::geography,
                                    poi.location::geography,
                                    1000
                                )
                            GROUP BY bs.id
                        ),
                        scenic_calculations AS (
                            SELECT 
                                bs.id,
                                spc.poi_count / GREATEST(bs.length_m, 1000.0) as poi_density,
                                spc.weighted_poi_sum / GREATEST(bs.length_m, 1000.0) as weighted_poi_density,
                                LEAST(10.0, 
                                    CASE
                                        WHEN bs.highway IN ('motorway', 'trunk', 'primary') THEN 3.0
                                        WHEN bs.highway IN ('secondary', 'tertiary') THEN 5.0
                                        WHEN bs.highway IN ('unclassified', 'residential') THEN 4.0
                                        WHEN bs.highway IN ('track', 'path', 'footway') THEN 7.0
                                        ELSE 5.0
                                    END + 
                                    COALESCE((spc.poi_count / GREATEST(bs.length_m, 1000.0)) * 0.5, 0)
                                ) as scenic_rating
                            FROM batch_segments bs
                            LEFT JOIN segment_poi_counts spc ON bs.id = spc.id
                        )
                        UPDATE gis_data_roadsegment rs
                        SET 
                            poi_density = sc.poi_density,
                            weighted_poi_density = sc.weighted_poi_density,
                            scenic_rating = COALESCE(sc.scenic_rating, 
                                CASE
                                    WHEN rs.highway IN ('motorway', 'trunk', 'primary') THEN 3.0
                                    WHEN rs.highway IN ('secondary', 'tertiary') THEN 5.0
                                    WHEN rs.highway IN ('unclassified', 'residential') THEN 4.0
                                    WHEN rs.highway IN ('track', 'path', 'footway') THEN 7.0
                                    ELSE 5.0
                                END)
                        FROM scenic_calculations sc
                        WHERE rs.id = sc.id
                    """)

                    batch_updated = cursor.rowcount
                    total_updated += batch_updated

                    batch_elapsed = time.time() - batch_start
                    current_progress = min(offset + batch_size, total_segments)
                    percent_complete = (current_progress / total_segments) * 100

                    logger.info(
                        f"Batch {offset//batch_size + 1}: Processed {batch_updated:,} segments "
                        f"({percent_complete:.1f}% complete) in {batch_elapsed:.1f}s"
                    )

                    # Track totals
                    if "poi_density_calculated" not in results:
                        results["poi_density_calculated"] = 0
                    results["poi_density_calculated"] += batch_updated

                    # Small pause to prevent DB overload
                    if offset + batch_size < total_segments and batch_elapsed < 5:
                        time.sleep(0.1)

                except Exception as e:
                    logger.error(f"Error processing batch starting at offset {offset}: {e}")
                    connection.rollback()
                    # Optionally continue with next batch
                    continue

        # Get final statistics
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT
                    ROUND(AVG(scenic_rating)::numeric, 2) as avg_scenic,
                    ROUND(AVG(poi_density)::numeric, 3) as avg_poi_density,
                    COUNT(CASE WHEN scenic_rating >= 8 THEN 1 END) as highly_scenic_count,
                    COUNT(CASE WHEN scenic_rating <= 3 THEN 1 END) as low_scenic_count,
                    COUNT(*) as total_processed
                FROM gis_data_roadsegment
                WHERE scenic_rating > 0 AND geometry IS NOT NULL
            """)

            stats = cursor.fetchone()
            results["average_scenic_rating"] = stats[0] or 0
            results["average_poi_density"] = stats[1] or 0
            results["highly_scenic_segments"] = stats[2] or 0
            results["low_scenic_segments"] = stats[3] or 0
            results["total_processed"] = stats[4] or 0
            results["scenic_ratings_assigned"] = total_updated
            results["scenic_ratings_enhanced"] = total_updated
            results["weighted_poi_density_calculated"] = total_updated

        total_elapsed = time.time() - overall_start_time
        logger.info(
            f"All scenic score calculations completed in "
            f"{total_elapsed:.1f}s ({total_elapsed / 60:.1f} min)"
        )
        logger.info(f"Batch-optimized calculation completed in {total_elapsed:.1f}s")

        return results

    @staticmethod
    def _get_comprehensive_metrics():
        """Get all comprehensive metrics in single query."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) as total_segments,
                    COUNT(CASE WHEN length_m > 0 THEN 1 END) as measured_segments,
                    ROUND(AVG(length_m)::numeric, 1) as avg_length,
                    ROUND(AVG(curvature)::numeric, 3) as avg_curvature,
                    ROUND(AVG(scenic_rating)::numeric, 2) as avg_scenic,
                    ROUND(AVG(poi_density)::numeric, 3) as avg_poi_density,
                    ROUND(MIN(scenic_rating)::numeric, 2) as min_scenic,
                    ROUND(MAX(scenic_rating)::numeric, 2) as max_scenic
                FROM gis_data_roadsegment
                WHERE geometry IS NOT NULL
            """
            )
            return cursor.fetchone()

    @staticmethod
    def get_metrics_summary():
        """Get comprehensive summary of all calculated metrics."""
        result = MetricsCalculator._get_comprehensive_metrics()

        return {
            "total_segments": result[0],
            "measured_segments": result[1],
            "average_length_m": result[2] or 0,
            "average_curvature": result[3] or 1.0,
            "average_scenic_rating": result[4] or 0,
            "average_poi_density": result[5] or 0,
            "min_scenic_rating": result[6] or 0,
            "max_scenic_rating": result[7] or 0,
            "scenic_range": (result[7] or 0) - (result[6] or 0),
        }

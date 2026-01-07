from django.db import connection


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
                SET curvature = CASE
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
                END
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

    @staticmethod
    def _update_poi_density():
        """Calculate and update POI density within 1km buffer."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE gis_data_roadsegment rs
                SET poi_density = (
                    SELECT COUNT(*)::float / GREATEST(rs.length_m, 1000)
                    FROM gis_data_pointofinterest poi
                    WHERE ST_DWithin(
                        rs.geometry::geography,
                        poi.location::geography,
                        1000
                    )
                )
                WHERE rs.geometry IS NOT NULL
            """
            )
            return cursor.rowcount

    @staticmethod
    def _update_weighted_poi_density():
        """Calculate and update weighted POI density (considering importance)."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE gis_data_roadsegment rs
                SET weighted_poi_density = (
                    SELECT COALESCE(SUM(poi.importance_score), 0)
                    / GREATEST(rs.length_m, 1000)
                    FROM gis_data_pointofinterest poi
                    WHERE ST_DWithin(
                        rs.geometry::geography,
                        poi.location::geography,
                        1000
                    )
                )
                WHERE rs.geometry IS NOT NULL
            """
            )
            return cursor.rowcount

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
            return cursor.rowcount

    @staticmethod
    def _enhance_scenic_with_poi_density():
        """Enhance scenic ratings with POI density bonus."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE gis_data_roadsegment
                SET scenic_rating = LEAST(10.0,
                    scenic_rating + (poi_density * 0.5)
                )
                WHERE poi_density > 0
            """
            )
            return cursor.rowcount

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
    def calculate_scenic_scores():
        """Calculate scenic quality scores for road segments."""
        results = {}

        results["poi_density_calculated"] = MetricsCalculator._update_poi_density()
        results[
            "weighted_poi_density_calculated"
        ] = MetricsCalculator._update_weighted_poi_density()
        results[
            "scenic_ratings_assigned"
        ] = MetricsCalculator._assign_base_scenic_ratings()
        results[
            "scenic_ratings_enhanced"
        ] = MetricsCalculator._enhance_scenic_with_poi_density()

        stats = MetricsCalculator._get_scenic_statistics()
        results["average_scenic_rating"] = stats[0] or 0
        results["average_poi_density"] = stats[1] or 0
        results["highly_scenic_segments"] = stats[2] or 0
        results["low_scenic_segments"] = stats[3] or 0

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

from django.db import connection


def get_elevation_at_point(latitude, longitude, table_name="dem"):
    """Get elevation at a specific point."""
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT ST_Value(rast, 1, ST_SetSRID(ST_Point(%s, %s), 4326))
            FROM {table_name}
            WHERE ST_Intersects(rast, ST_SetSRID(ST_Point(%s, %s), 4326))
            LIMIT 1;
        """,
            [longitude, latitude, longitude, latitude],
        )

        result = cursor.fetchone()
        if result and result[0] is not None:
            return float(result[0]), True
        return None, False


def get_dem_statistics(table_name="dem"):
    """Get overall DEM statistics."""
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                COUNT(*) as tile_count,
                MIN(ST_SummaryStats(rast))->>'min' as global_min,
                MAX(ST_SummaryStats(rast))->>'max' as global_max,
                AVG((ST_SummaryStats(rast)).mean) as global_mean
            FROM {table_name};
        """
        )
        return cursor.fetchone()

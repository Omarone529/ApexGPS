import logging

import polyline
from django.contrib.gis.geos import LineString, Point
from django.db import connection

logger = logging.getLogger(__name__)

__all__ = [
    "_validate_coordinates",
    "_find_nearest_vertex",
    "_get_road_segment_by_id",
    "_get_road_segment_by_vertices",
    "_calculate_path_metrics",
    "_encode_linestring_to_polyline",
    "_extract_coordinates_from_wkt",
    "_create_linestring_from_coords",
]


def _validate_coordinates(lat: float, lon: float) -> tuple[bool, str]:
    """Validate geographic coordinates."""
    if not (-90 <= lat <= 90):
        return False, f"Latitude {lat} is out of valid range (-90 to 90)"

    if not (-180 <= lon <= 180):
        return False, f"Longitude {lon} is out of valid range (-180 to 180)"

    return True, ""


def _find_nearest_vertex(point: Point, distance_threshold: float = 0.01) -> int | None:
    """Find nearest routing vertex to a geographic point."""
    lon, lat = point.x, point.y

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id
            FROM gis_data_roadsegment_vertices_pgr
            WHERE ST_DWithin(the_geom, ST_MakePoint(%s, %s), %s)
            ORDER BY ST_Distance(the_geom, ST_MakePoint(%s, %s))
            LIMIT 1
            """,
            [lon, lat, distance_threshold, lon, lat],
        )

        result = cursor.fetchone()
        return result[0] if result else None


def _get_road_segment_by_id(segment_id: int) -> dict | None:
    """Get road segment by its ID."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                id, osm_id, name, highway, length_m,
                cost_time, scenic_rating, curvature,
                ST_AsText(geometry) as geometry_wkt
            FROM gis_data_roadsegment
            WHERE id = %s
            """,
            [segment_id],
        )

        row = cursor.fetchone()
        if not row:
            return None

        return _row_to_segment_dict(row)


def _get_road_segment_by_vertices(
    source_vertex: int, target_vertex: int
) -> dict | None:
    """Get road segment between two vertices."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                id, osm_id, name, highway, length_m,
                cost_time, scenic_rating, curvature,
                ST_AsText(geometry) as geometry_wkt
            FROM gis_data_roadsegment
            WHERE (source = %s AND target = %s)
            OR (source = %s AND target = %s AND oneway = false)
            LIMIT 1
            """,
            [source_vertex, target_vertex, target_vertex, source_vertex],
        )

        row = cursor.fetchone()
        if not row:
            return None

        return _row_to_segment_dict(row)


def _row_to_segment_dict(row: tuple) -> dict:
    """Convert database row to segment dictionary."""
    segment = {
        "id": row[0],
        "osm_id": row[1],
        "name": row[2],
        "highway": row[3],
        "length_m": float(row[4]),
        "cost_time": float(row[5]),
        "scenic_rating": float(row[6]) if row[6] else 0.0,
        "curvature": float(row[7]) if row[7] else 0.0,
    }

    # Parse geometry if available
    if row[8]:
        segment["geometry_coords"] = _extract_coordinates_from_wkt(row[8])

    return segment


def _extract_coordinates_from_wkt(wkt: str) -> list[tuple[float, float]]:
    """Extract coordinates from WKT LINESTRING."""
    if not wkt or not wkt.startswith("LINESTRING"):
        return []

    try:
        coord_str = wkt.replace("LINESTRING(", "").replace(")", "")
        # Split by commas and parse coordinates
        coords = []
        for point in coord_str.split(","):
            x, y = map(float, point.strip().split())
            coords.append((x, y))  # (lon, lat)
        return coords
    except Exception as e:
        logger.error(f"Error parsing WKT: {e}")
        return []


def _calculate_path_metrics(segments: list[dict]) -> dict:
    """Calculate total metrics for a list of road segments."""
    if not segments:
        return {
            "total_distance_m": 0.0,
            "total_distance_km": 0.0,
            "total_time_seconds": 0.0,
            "total_time_minutes": 0.0,
            "segment_count": 0,
        }

    total_distance_m = sum(seg.get("length_m", 0) for seg in segments)
    total_time_seconds = sum(seg.get("cost_time", 0) for seg in segments)

    return {
        "total_distance_m": total_distance_m,
        "total_distance_km": total_distance_m / 1000,
        "total_time_seconds": total_time_seconds,
        "total_time_minutes": total_time_seconds / 60,
        "segment_count": len(segments),
    }


def _encode_linestring_to_polyline(geometry: LineString) -> str:
    """Convert LineString geometry to encoded polyline."""
    if not geometry or geometry.empty:
        return ""

    # geometry.coords returns (x, y) = (lon, lat)
    # polyline.encode expects (lat, lon)
    lat_lon_coords = [(lat, lon) for lon, lat in geometry.coords]
    return polyline.encode(lat_lon_coords)


def _create_linestring_from_coords(
    coords: list[tuple[float, float]]
) -> LineString | None:
    """Create LineString from list of (lon, lat) coordinates."""
    if not coords:
        return None

    unique_coords = []
    for i, coord in enumerate(coords):
        if i == 0 or coord != coords[i - 1]:
            unique_coords.append(coord)

    return LineString(unique_coords) if unique_coords else None


def _execute_dijkstra_query(
    start_vertex: int, end_vertex: int, cost_column: str = "cost_time"
) -> list[tuple]:
    """Execute Dijkstra algorithm query and return results."""
    with connection.cursor() as cursor:
        query = f"""
            SELECT seq, path_seq, node, edge, cost, agg_cost
            FROM pgr_dijkstra(
                'SELECT id, source, target, {cost_column} as cost,
                 {cost_column} as reverse_cost
                 FROM gis_data_roadsegment
                 WHERE geometry IS NOT NULL
                 AND source IS NOT NULL
                 AND target IS NOT NULL
                 AND is_active = true',
                %s, %s, directed := true
            )
            ORDER BY seq
        """
        cursor.execute(query, [start_vertex, end_vertex])
        return cursor.fetchall()


def _extract_edges_from_dijkstra_result(dijkstra_result: list[tuple]) -> list[int]:
    """Extract edge IDs from Dijkstra result."""
    if not dijkstra_result:
        return []

    # edge column is at index 3, exclude -1 values
    return [row[3] for row in dijkstra_result if row[3] >= 0]


def _get_segments_by_ids(segment_ids: list[int]) -> list[dict]:
    """Get multiple road segments by their IDs in order."""
    if not segment_ids:
        return []

    with connection.cursor() as cursor:
        # Create array of IDs for PostgreSQL
        ids_array = "{" + ",".join(map(str, segment_ids)) + "}"

        query = """
            SELECT
                id, osm_id, name, highway, length_m,
                cost_time, scenic_rating, curvature,
                ST_AsText(geometry) as geometry_wkt
            FROM gis_data_roadsegment
            WHERE id = ANY(%s::int[])
            ORDER BY array_position(%s::int[], id)
        """

        cursor.execute(query, [ids_array, ids_array])
        rows = cursor.fetchall()

    return [_row_to_segment_dict(row) for row in rows]


def _create_route_geometry(segments: list[dict]) -> LineString | None:
    """Create complete route geometry from segments."""
    all_coords = []

    for segment in segments:
        coords = segment.get("geometry_coords", [])
        if coords:
            all_coords.extend(coords)

    return _create_linestring_from_coords(all_coords)

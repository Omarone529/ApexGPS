import polyline
from django.db import connection


def find_nearest_vertex(point, distance_threshold=0.01):
    """Find nearest routing vertex to point."""
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


def get_road_segment(source_vertex, target_vertex):
    """Get road segment between two vertices."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                id, osm_id, name, highway, length_m,
                cost_time, scenic_rating, curvature, geometry
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

        return {
            "id": row[0],
            "osm_id": row[1],
            "name": row[2],
            "highway": row[3],
            "length_m": row[4],
            "cost_time": row[5],
            "scenic_rating": row[6],
            "curvature": row[7],
            "geometry": row[8],
        }


def calculate_dijkstra_path(start_vertex, end_vertex, cost_column="cost_time"):
    """Calculate path using Dijkstra's algorithm."""
    with connection.cursor() as cursor:
        query = f"""
            SELECT * FROM pgr_dijkstra(
                'SELECT id, source, target, {cost_column} as cost,
                 {cost_column} as reverse_cost
                 FROM gis_data_roadsegment
                 WHERE geometry IS NOT NULL
                 AND source IS NOT NULL
                 AND target IS NOT NULL
                 AND is_active = true',
                %s, %s, directed := true
            )
        """
        cursor.execute(query, [start_vertex, end_vertex])
        return cursor.fetchall()


def extract_vertex_ids(dijkstra_result):
    """Extract vertex IDs from Dijkstra result."""
    if not dijkstra_result:
        return []
    return [row[2] for row in dijkstra_result]


def get_path_segments(vertex_ids):
    """Get road segments for a list of vertex IDs."""
    if len(vertex_ids) < 2:
        return []

    segments = []
    for i in range(len(vertex_ids) - 1):
        segment = get_road_segment(vertex_ids[i], vertex_ids[i + 1])
        if segment:
            segments.append(segment)

    return segments


def calculate_total_distance(segments):
    """Calculate total distance of path segments."""
    return sum(seg["length_m"] for seg in segments)


def calculate_total_time(segments):
    """Calculate total time of path segments."""
    return sum(seg["cost_time"] for seg in segments)


def encode_path_to_polyline(segments):
    """Convert path segments to polyline encoding."""
    coordinates = []
    for seg in segments:
        if seg["geometry"]:
            coords = list(seg["geometry"].coords)
            coordinates.extend(coords)

    unique_coords = []
    for coord in coordinates:
        if coord not in unique_coords:
            unique_coords.append(coord)

    return polyline.encode([(lat, lon) for lon, lat in unique_coords])


def calculate_shortest_path(start_point, end_point, cost_column="cost_time"):
    """Calculate shortest path between two points."""
    start_vertex = find_nearest_vertex(start_point)
    end_vertex = find_nearest_vertex(end_point)

    if not start_vertex or not end_vertex:
        return None

    dijkstra_result = calculate_dijkstra_path(start_vertex, end_vertex, cost_column)
    if not dijkstra_result:
        return None

    vertex_ids = extract_vertex_ids(dijkstra_result)
    segments = get_path_segments(vertex_ids)

    total_distance = calculate_total_distance(segments)
    total_time = calculate_total_time(segments)
    polyline_encoded = encode_path_to_polyline(segments)

    return {
        "start_vertex": start_vertex,
        "end_vertex": end_vertex,
        "total_distance_m": total_distance,
        "total_distance_km": total_distance / 1000,
        "total_time_seconds": total_time,
        "total_time_minutes": total_time / 60,
        "segment_count": len(segments),
        "polyline": polyline_encoded,
        "segments": segments,
    }


def calculate_fastest_route(start_point, end_point):
    """Calculate fastest route between two points."""
    return calculate_shortest_path(start_point, end_point, "cost_time")


def calculate_shortest_route(start_point, end_point):
    """Calculate shortest route by distance."""
    return calculate_shortest_path(start_point, end_point, "cost_length")

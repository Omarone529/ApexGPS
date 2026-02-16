import polyline
import json
from django.db import connection
# from django.contrib.gis.geos import Point


def find_nearest_vertex(point, distance_threshold=0.01, table_name='gis_data_roadsegment'):
    """
    Find nearest routing vertex using optimized query.
    """
    lon, lat = point.x, point.y
    vertices_table = f'{table_name}_vertices_pgr'

    with connection.cursor() as cursor:
        cursor.execute(f"""
            SELECT id, geom
            FROM {vertices_table}
            ORDER BY geom <-> ST_SetSRID(ST_Point(%s, %s), 4326)
            LIMIT 1
        """, [lon, lat])

        result = cursor.fetchone()
        if result:
            return result[0], result[1]
        return None, None


def calculate_route(start_point, end_point, cost_column='cost_time',
                    table_name='gis_data_roadsegment', directed=True):
    """
    Calculate route using pgRouting with optimized queries.

    Returns: {
        'geometry': GeoJSON LineString,
        'distance': total distance in meters,
        'time': total time in seconds,
        'segments': list of segment details
    }

    Unifies old methods as:
        -get_road_segments
        -calculate_dijkstra_path
        -extract_vertex_ids
        -get_path_segments
        -calculate_total_distance
        -calculate_total_time
        -encode_path_to_polyline
    """
    # Find nearest vertices
    start_vertex, start_geom = find_nearest_vertex(start_point, table_name=table_name)
    end_vertex, end_geom = find_nearest_vertex(end_point, table_name=table_name)

    if not start_vertex or not end_vertex:
        return None

    # Calculate route using Dijkstra
    with connection.cursor() as cursor:
        # Use pgRouting v4.0 syntax
        query = f"""
            WITH route AS (
                SELECT 
                    seq,
                    path_seq,
                    node,
                    edge,
                    cost,
                    agg_cost
                FROM pgr_dijkstra(
                    'SELECT 
                        id, 
                        source, 
                        target, 
                        {cost_column} AS cost,
                        {cost_column} AS reverse_cost
                     FROM {table_name}
                     WHERE geometry IS NOT NULL
                     AND source IS NOT NULL
                     AND target IS NOT NULL',
                    %s, %s, directed := %s
                )
            )
            SELECT 
                r.seq,
                r.node,
                r.edge,
                r.cost,
                r.agg_cost,
                s.name,
                s.highway,
                s.length_m,
                s.scenic_rating,
                s.curvature,
                ST_AsGeoJSON(s.geometry) AS geometry
            FROM route r
            JOIN {table_name} s ON r.edge = s.id
            ORDER BY r.seq
        """

        cursor.execute(query, [start_vertex, end_vertex, directed])
        rows = cursor.fetchall()

        if not rows:
            return None

        # Format results
        segments = []
        total_distance = 0
        total_time = 0
        coordinates = []

        for row in rows:
            segment = {
                'id': row[2],  # edge id
                'node': row[1],
                'name': row[5],
                'highway': row[6],
                'length_m': float(row[7]) if row[7] else 0,
                'scenic_rating': float(row[8]) if row[8] else 0,
                'curvature': float(row[9]) if row[9] else 1.0,
                'cost': float(row[3]) if row[3] else 0,
            }
            segments.append(segment)

            total_distance += segment['length_m']
            total_time += segment['cost']

            # Parse geometry
            if row[10]:
                try:
                    geom_data = json.loads(row[10])
                    if geom_data['type'] == 'LineString':
                        # Convert [lon, lat] to [lat, lon] for polyline
                        for coord in geom_data['coordinates']:
                            coordinates.append((coord[1], coord[0]))  # lat, lon
                except:
                    pass

        # Create GeoJSON geometry
        if coordinates:
            # Convert back to [lon, lat] for GeoJSON
            geojson_coords = [[lon, lat] for lat, lon in coordinates]
            geometry = {
                'type': 'LineString',
                'coordinates': geojson_coords
            }

            # Create polyline encoding
            polyline_encoded = polyline.encode(coordinates)
        else:
            geometry = None
            polyline_encoded = None

        return {
            'start_vertex': start_vertex,
            'end_vertex': end_vertex,
            'total_distance_m': total_distance,
            'total_distance_km': total_distance / 1000,
            'total_time_seconds': total_time,
            'total_time_minutes': total_time / 60,
            'segment_count': len(segments),
            'geometry': geometry,
            'polyline': polyline_encoded,
            'segments': segments
        }


def calculate_scenic_route(start_point, end_point, scenic_weight=2.0):
    """
    Calculate scenic route using custom cost function.

    Custom cost = time_cost - (scenic_rating * scenic_weight * 10)
    Higher scenic_weight means more preference for scenic routes.
    """
    start_vertex, _ = find_nearest_vertex(start_point)
    end_vertex, _ = find_nearest_vertex(end_point)

    if not start_vertex or not end_vertex:
        return None

    with connection.cursor() as cursor:
        # Use custom cost calculation in SQL
        query = f"""
            WITH scenic_costs AS (
                SELECT 
                    id,
                    source,
                    target,
                    GREATEST(0.1, cost_time - (scenic_rating * %s * 10)) AS cost,
                    GREATEST(0.1, cost_time - (scenic_rating * %s * 10)) AS reverse_cost
                FROM gis_data_roadsegment
                WHERE geometry IS NOT NULL
                AND source IS NOT NULL
                AND target IS NOT NULL
            )
            SELECT 
                r.seq,
                r.node,
                r.edge,
                r.cost,
                r.agg_cost,
                s.name,
                s.highway,
                s.length_m,
                s.scenic_rating,
                s.curvature,
                ST_AsGeoJSON(s.geometry) AS geometry
            FROM pgr_dijkstra(
                'SELECT * FROM scenic_costs',
                %s, %s, directed := true
            ) r
            JOIN gis_data_roadsegment s ON r.edge = s.id
            ORDER BY r.seq
        """

        cursor.execute(query, [scenic_weight, scenic_weight, start_vertex, end_vertex])
        rows = cursor.fetchall()

        # Format results (same as above)
        return _format_route_result(rows)


def _format_route_result(rows):
    """Helper to format route results."""
    if not rows:
        return None

    segments = []
    total_distance = 0
    coordinates = []

    for row in rows:
        segment = {
            'id': row[2],
            'node': row[1],
            'name': row[5],
            'highway': row[6],
            'length_m': float(row[7]) if row[7] else 0,
            'scenic_rating': float(row[8]) if row[8] else 0,
            'curvature': float(row[9]) if row[9] else 1.0,
        }
        segments.append(segment)
        total_distance += segment['length_m']

        if row[10]:
            try:
                geom_data = json.loads(row[10])
                if geom_data['type'] == 'LineString':
                    for coord in geom_data['coordinates']:
                        coordinates.append((coord[1], coord[0]))
            except:
                pass

    if coordinates:
        geojson_coords = [[lon, lat] for lat, lon in coordinates]
        geometry = {
            'type': 'LineString',
            'coordinates': geojson_coords
        }
        polyline_encoded = polyline.encode(coordinates)
    else:
        geometry = None
        polyline_encoded = None

    return {
        'total_distance_m': total_distance,
        'total_distance_km': total_distance / 1000,
        'segment_count': len(segments),
        'geometry': geometry,
        'polyline': polyline_encoded,
        'segments': segments
    }


# Backward compatibility functions
def calculate_shortest_path(start_point, end_point, cost_column="cost_time"):
    """Alias for backward compatibility."""
    return calculate_route(start_point, end_point, cost_column)


def calculate_fastest_route(start_point, end_point):
    """Calculate fastest route."""
    return calculate_route(start_point, end_point, 'cost_time')


def calculate_scenic_shortest_route(start_point, end_point):
    """Calculate scenic route."""
    return calculate_route(start_point, end_point, 'cost_scenic')
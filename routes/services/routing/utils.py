from concurrent.futures import ThreadPoolExecutor, as_completed
from django.contrib.gis.geos import LineString, Point
from django.db import connection
import logging
import polyline
import re
import requests
from rest_framework import status
from rest_framework.response import Response

from routes.models import Route

logger = logging.getLogger(_name_)

_all_ = [
    "_validate_coordinates",
    "_find_nearest_vertex",
    "_get_road_segment_by_id",
    "_get_road_segment_by_vertices",
    "_row_to_segment_dict",
    "_calculate_path_metrics",
    "_encode_linestring_to_polyline",
    "_extract_coordinates_from_wkt",
    "_create_linestring_from_coords",
    "_execute_dijkstra_query",
    "_extract_edges_from_dijkstra_result",
    "_get_segments_by_ids",
    "_create_route_geometry",
    "_get_segments_with_scenic_data",
    "_calculate_route_scenic_stats",
    "_compare_routes_scenic_quality",
    "_is_secondary_road",
    "_calculate_segment_secondary_length",
    "_calculate_total_route_length",
    "_calculate_secondary_road_length",
    "_get_secondary_road_percentage",
    "_prepare_route_response",
    "_is_relevant_photo",
    "_fetch_wikimedia_geosearch",
    "_fetch_pic4carto",
    "_fetch_wikipedia_description",
    "_fetch_wikipedia_image",
    "_compute_straight_distance_km",
    "_check_route_ownership",
    "_routing_services_unavailable"
]


def _validate_coordinates(lat: float, lon: float) -> tuple[bool, str]:
    if not (-90 <= lat <= 90):
        return False, f"Latitude {lat} is out of valid range (-90 to 90)"

    if not (-180 <= lon <= 180):
        return False, f"Longitude {lon} is out of valid range (-180 to 180)"

    return True, ""


def _find_nearest_vertex(point: Point, distance_threshold: float = 0.01) -> int | None:
    point_wkt = f"SRID=4326;POINT({point.x} {point.y})"

    queries = [
        # Find vertices with >= 3 connections on drivable roads (most reliable)
        """
        SELECT v.id, COUNT(r.id) as connections
        FROM gis_data_roadsegment_vertices_pgr v
        LEFT JOIN gis_data_roadsegment r ON
            (r.source = v.id OR r.target = v.id)
            AND r.is_active = true
            AND r.highway NOT IN ('footway', 'path', 'cycleway', 'steps')
        WHERE ST_DWithin(v.geom, ST_GeomFromEWKT(%s), %s)
        GROUP BY v.id, v.geom
        HAVING COUNT(r.id) >= 3
        ORDER BY COUNT(r.id) DESC, ST_Distance(v.geom, ST_GeomFromEWKT(%s))
        LIMIT 1
        """,
        # Find vertices with >= 2 connections on drivable roads
        """
        SELECT v.id, COUNT(r.id) as connections
        FROM gis_data_roadsegment_vertices_pgr v
        LEFT JOIN gis_data_roadsegment r ON
            (r.source = v.id OR r.target = v.id)
            AND r.is_active = true
            AND r.highway NOT IN ('footway', 'path', 'cycleway', 'steps')
        WHERE ST_DWithin(v.geom, ST_GeomFromEWKT(%s), %s)
        GROUP BY v.id, v.geom
        HAVING COUNT(r.id) >= 2
        ORDER BY COUNT(r.id) DESC, ST_Distance(v.geom, ST_GeomFromEWKT(%s))
        LIMIT 1
        """,
        # Find any vertex with at least 1 connection
        """
        SELECT v.id, COUNT(r.id) as connections
        FROM gis_data_roadsegment_vertices_pgr v
        LEFT JOIN gis_data_roadsegment r ON
            (r.source = v.id OR r.target = v.id)
            AND r.is_active = true
        WHERE ST_DWithin(v.geom, ST_GeomFromEWKT(%s), %s)
        GROUP BY v.id, v.geom
        HAVING COUNT(r.id) >= 1
        ORDER BY COUNT(r.id) DESC, ST_Distance(v.geom, ST_GeomFromEWKT(%s))
        LIMIT 1
        """,
        # find ANY vertex
        """
        SELECT v.id
        FROM gis_data_roadsegment_vertices_pgr v
        WHERE ST_DWithin(v.geom, ST_GeomFromEWKT(%s), %s)
        ORDER BY ST_Distance(v.geom, ST_GeomFromEWKT(%s))
        LIMIT 1
        """,
    ]

    for i, query in enumerate(queries):
        try:
            with connection.cursor() as cursor:
                cursor.execute(query, [point_wkt, distance_threshold, point_wkt])
                result = cursor.fetchone()

                if result:
                    vertex_id = result[0]
                    connections = result[1] if len(result) > 1 else "unknown"
                    logger.debug(
                        f"Found vertex {vertex_id} with {connections} "
                        f"connections (query {i + 1})"
                    )
                    return vertex_id

        except Exception as e:
            logger.warning(f"Query {i + 1} failed: {str(e)}")
            continue

    if distance_threshold < 0.02:
        logger.debug(
            f"No vertex found with threshold {distance_threshold}, trying 0.02"
        )
        return _find_nearest_vertex(point, distance_threshold=0.02)

    logger.warning(f"No vertices found within 0.02 degrees of point {point}")
    return None


def _get_road_segment_by_id(segment_id: int) -> dict | None:
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

    if row[8]:
        segment["geometry_coords"] = _extract_coordinates_from_wkt(row[8])

    return segment


def _extract_coordinates_from_wkt(wkt: str) -> list[tuple[float, float]]:
    if not wkt or not wkt.startswith("LINESTRING"):
        return []

    try:
        coord_str = wkt.replace("LINESTRING(", "").replace(")", "")
        coords = []
        for point in coord_str.split(","):
            x, y = map(float, point.strip().split())
            coords.append((x, y))
        return coords
    except Exception as e:
        logger.error(f"Error parsing WKT: {e}")
        return []


def _calculate_path_metrics(segments: list[dict]) -> dict:
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
    if not geometry or geometry.empty:
        return ""

    lat_lon_coords = [(lat, lon) for lon, lat in geometry.coords]
    return polyline.encode(lat_lon_coords)


def _create_linestring_from_coords(
    coords: list[tuple[float, float]]
) -> LineString | None:
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
    with connection.cursor() as cursor:
        escaped_cost_column = cost_column.replace("'", "''")

        query = f"""
            SELECT seq, path_seq, node, edge, cost, agg_cost
            FROM pgr_dijkstra(
                'SELECT id, source, target, {escaped_cost_column} as cost,
                 {escaped_cost_column} as reverse_cost
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
    if not dijkstra_result:
        return []

    return [row[3] for row in dijkstra_result if row[3] >= 0]


def _get_segments_by_ids(segment_ids: list[int]) -> list[dict]:
    if not segment_ids:
        return []

    with connection.cursor() as cursor:
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
    all_coords = []

    for segment in segments:
        coords = segment.get("geometry_coords", [])
        if not coords:
            continue

        if all_coords:
            last = all_coords[-1]
            first = coords[0]
            last_reversed = coords[-1]

            dist_normal = abs(first[0] - last[0]) + abs(first[1] - last[1])
            dist_reversed = abs(last_reversed[0] - last[0]) + abs(last_reversed[1] - last[1])

            if dist_reversed < dist_normal:
                coords = list(reversed(coords))

        all_coords.extend(coords)

    return _create_linestring_from_coords(all_coords)


def _format_time_minutes(minutes: float) -> str:
    hours = int(minutes // 60)
    mins = int(minutes % 60)

    if hours > 0:
        return f"{hours}h {mins}min"
    return f"{mins}min"


def _format_distance_km(distance_km: float) -> str:
    if distance_km >= 100:
        return f"{distance_km:.0f} km"
    elif distance_km >= 10:
        return f"{distance_km:.1f} km"
    else:
        return f"{distance_km:.2f} km"


def _calculate_route_segments(routing_service, points: list) -> dict:
    if len(points) < 2:
        return {
            "success": False,
            "error": "Need at least 2 points",
            "total_distance_km": 0,
            "total_time_minutes": 0,
        }

    total_distance_km = 0
    total_time_minutes = 0
    segments_info = []

    for i in range(len(points) - 1):
        start_point = points[i]
        end_point = points[i + 1]

        segment_result = routing_service.calculate_route(
            start_point=start_point,
            end_point=end_point,
            vertex_threshold=0.01,
        )

        if segment_result:
            segment_distance = segment_result.get("total_distance_km", 0)
            segment_time = segment_result.get("total_time_minutes", 0)

            total_distance_km += segment_distance
            total_time_minutes += segment_time

            segments_info.append(
                {
                    "index": i,
                    "distance_km": segment_distance,
                    "time_minutes": segment_time,
                    "success": True,
                }
            )
        else:
            segments_info.append(
                {
                    "index": i,
                    "distance_km": 0,
                    "time_minutes": 0,
                    "success": False,
                    "error": f"No route found from point {i} to {i + 1}",
                }
            )

    return {
        "success": True,
        "total_distance_km": total_distance_km,
        "total_time_minutes": total_time_minutes,
        "segment_count": len(segments_info),
        "segments": segments_info,
        "all_segments_valid": all(seg.get("success", False) for seg in segments_info),
    }


def _prepare_route_response(
    fastest_route: dict,
    start_data: dict,
    end_data: dict,
    validation_result: dict,
    processing_time: float,
) -> dict:
    return {
        "route_type": "fastest",
        "purpose": "baseline_for_scenic_routes",
        "start_location": start_data.get("name"),
        "end_location": end_data.get("name"),
        "start_coordinates": {
            "lat": start_data.get("lat"),
            "lon": start_data.get("lon"),
        },
        "end_coordinates": {
            "lat": end_data.get("lat"),
            "lon": end_data.get("lon"),
        },
        "total_distance_km": fastest_route.get("total_distance_km", 0),
        "total_time_minutes": fastest_route.get("total_time_minutes", 0),
        "total_distance_m": fastest_route.get("total_distance_m", 0),
        "total_time_seconds": fastest_route.get("total_time_seconds", 0),
        "segment_count": fastest_route.get("segment_count", 0),
        "total_segments": fastest_route.get("total_segments", 0),
        "total_distance_formatted": _format_distance_km(
            fastest_route.get("total_distance_km", 0)
        ),
        "total_time_formatted": _format_time_minutes(
            fastest_route.get("total_time_minutes", 0)
        ),
        "polyline": fastest_route.get("polyline", ""),
        "has_geometry": fastest_route.get("geometry") is not None,
        "start_vertex": fastest_route.get("start_vertex"),
        "end_vertex": fastest_route.get("end_vertex"),
        "vertex_count": fastest_route.get("vertex_count", 0),
        "validation": {
            "is_valid": validation_result.get("is_valid", False),
            "warnings": validation_result.get("warnings", []),
            "start_vertex": validation_result.get("start_vertex"),
            "end_vertex": validation_result.get("end_vertex"),
        },
        "processing_time_ms": round(processing_time * 1000, 2),
        "database_status": "real_osm_data",
        "geocoding_status": {
            "start_geocoded": start_data.get("geocoded", False),
            "end_geocoded": end_data.get("geocoded", False),
            "start_original_name": start_data.get("original_name", ""),
            "end_original_name": end_data.get("original_name", ""),
        },
    }


def _get_segments_with_scenic_data(edge_ids: list[int]) -> list[dict]:
    if not edge_ids:
        return []

    with connection.cursor() as cursor:
        ids_array = "{" + ",".join(map(str, edge_ids)) + "}"

        query = """
            SELECT
                id, osm_id, name, highway, length_m,
                cost_time, scenic_rating, curvature,
                ST_AsText(geometry) as geometry_wkt
            FROM gis_data_roadsegment
            WHERE id = ANY(%s::int[])
            AND (scenic_rating IS NOT NULL OR curvature IS NOT NULL)
            ORDER BY array_position(%s::int[], id)
        """

        cursor.execute(query, [ids_array, ids_array])
        rows = cursor.fetchall()

    return [_row_to_segment_dict(row) for row in rows]


def _calculate_route_scenic_stats(segments: list[dict]) -> dict:
    if not segments:
        return {
            "has_scenic_data": False,
            "scenic_segment_count": 0,
            "total_scenic_score": 0.0,
            "scenic_breakdown": {},
        }

    scenic_segments = [s for s in segments if s.get("scenic_rating") is not None]
    curvy_segments = [s for s in segments if s.get("curvature", 0) > 0.7]

    rating_distribution = {}
    for segment in scenic_segments:
        rating = segment.get("scenic_rating", 0)
        rating_key = f"rating_{int(rating)}"
        rating_distribution[rating_key] = rating_distribution.get(rating_key, 0) + 1

    length_by_quality = {"high": 0.0, "medium": 0.0, "low": 0.0}
    for segment in segments:
        rating = segment.get("scenic_rating", 2.5)
        length = segment.get("length_m", 0)

        if rating >= 4.0:
            length_by_quality["high"] += length
        elif rating >= 2.5:
            length_by_quality["medium"] += length
        else:
            length_by_quality["low"] += length

    total_length = sum(length_by_quality.values())
    if total_length > 0:
        for key in length_by_quality:
            length_by_quality[key] = round(
                length_by_quality[key] / total_length * 100, 1
            )

    curvature_values = [
        s.get("curvature", 0) for s in segments if s.get("curvature") is not None
    ]
    avg_curvature = (
        sum(curvature_values) / len(curvature_values) if curvature_values else 0.0
    )

    return {
        "has_scenic_data": len(scenic_segments) > 0,
        "scenic_segment_count": len(scenic_segments),
        "total_segments": len(segments),
        "scenic_coverage_percent": round(len(scenic_segments) / len(segments) * 100, 1)
        if segments
        else 0,
        "curvy_segment_count": len(curvy_segments),
        "curvy_segment_percent": round(len(curvy_segments) / len(segments) * 100, 1)
        if segments
        else 0,
        "avg_curvature": round(avg_curvature, 3),
        "scenic_rating_distribution": rating_distribution,
        "length_by_scenic_quality": length_by_quality,
    }


def _compare_routes_scenic_quality(
    route1_segments: list[dict], route2_segments: list[dict]
) -> dict:
    def calculate_route_score(segments):
        if not segments:
            return 0.0

        total_score = 0.0
        total_length = 0.0

        for segment in segments:
            rating = segment.get("scenic_rating", 2.5)
            curvature = segment.get("curvature", 0.5)
            length = segment.get("length_m", 0)

            score = rating * (1.0 + curvature)

            total_score += score * length
            total_length += length

        if total_length == 0:
            return 0.0

        avg_score = total_score / total_length
        return (avg_score / 5.0) * 100

    score1 = calculate_route_score(route1_segments)
    score2 = calculate_route_score(route2_segments)

    score_difference = score2 - score1
    percent_difference = (score_difference / score1 * 100) if score1 > 0 else 0

    return {
        "route1_score": round(score1, 1),
        "route2_score": round(score2, 1),
        "score_difference": round(score_difference, 1),
        "percent_difference": round(percent_difference, 1),
        "better_route": "route1"
        if score1 > score2
        else "route2"
        if score2 > score1
        else "equal",
    }


def _is_secondary_road(highway_type: str) -> bool:
    secondary_types = {
        "secondary",
        "tertiary",
        "unclassified",
        "road",
        "track",
        "path",
        "service",
        "residential",
        "living_street",
    }
    return highway_type in secondary_types


def _calculate_segment_secondary_length(segment: dict) -> float:
    length = segment.get("length_m", 0)
    highway = segment.get("highway", "")

    if _is_secondary_road(highway):
        return length
    return 0.0


def _calculate_total_route_length(segments: list[dict]) -> float:
    if not segments:
        return 0.0

    total_length = 0.0
    for segment in segments:
        total_length += segment.get("length_m", 0)
    return total_length


def _calculate_secondary_road_length(segments: list[dict]) -> float:
    if not segments:
        return 0.0

    secondary_length = 0.0
    for segment in segments:
        secondary_length += _calculate_segment_secondary_length(segment)
    return secondary_length


def _get_secondary_road_percentage(segments: list[dict]) -> float:
    if not segments:
        return 0.0

    total_length = _calculate_total_route_length(segments)
    secondary_length = _calculate_secondary_road_length(segments)

    if total_length == 0:
        return 0.0

    return (secondary_length / total_length) * 100

# Regex for fast filtering
EXCLUDE_PATTERNS = re.compile(
    r'portrait|ritratto|selfie|foto di gruppo|person|persona|people|gente|'
    r'uomo|donna|man|woman|child|bambino|family|famiglia|viso|face|profile|'
    r'profilo|autoritratto|wedding|matrimonio|party|festa|group|gruppo|'
    r'tourist|turista|visitor|visitatore|crowd|folla|ritratto fotografico|'
    r'photographic portrait|headshot',
    re.IGNORECASE
)

PLACE_PATTERNS = re.compile(
    r'church|chiesa|cathedral|duomo|basilica|castle|castello|fortress|fortezza|'
    r'monument|monumento|statua|statue|museum|museo|gallery|galleria|view|vista|'
    r'panorama|panoramic|lake|lago|river|fiume|waterfall|cascata|mountain|montagna|'
    r'hill|colle|pass|passo|vineyard|vigneto|wine|vino|square|piazza|street|via|'
    r'road|strada|building|edificio|palace|palazzo|park|parco|garden|giardino|'
    r'bridge|ponte|tower|torre|ruins|rovine|archaeological|archeologico|fountain|'
    r'fontana|well|pozzo|coast|costa|sea|mare|beach|spiaggia|valley|valle|cliff|'
    r'scogliera|restaurant|ristorante|trattoria|osteria|taverna|locanda|food|cibo|'
    r'pizza|pizzeria|eating|mangiare|cafe|caffè|coffee|bar|pub|brewery|birreria|'
    r'wine bar|enoteca|gelateria|ice cream|pastry|pasticceria|bakery|forno|meal|'
    r'pranzo|cena|dinner|lunch',
    re.IGNORECASE
)


def _is_relevant_photo(title, description=""):
    """Return True if photo is location-appropriate (not a portrait)."""
    text = f"{title} {description}".lower()
    if EXCLUDE_PATTERNS.search(text):
        return False
    if PLACE_PATTERNS.search(text):
        return True
    return True


def _fetch_wikipedia_image(name, wikipedia_url, headers):
    """
    Fetch the main image from the Wikipedia page matching the given name.
    Returns a photo dict or None.
    """
    if not name:
        return None

    # Search for the page by name
    params_search = {
        "action": "query",
        "format": "json",
        "list": "search",
        "srsearch": name,
        "srlimit": 1
    }
    try:
        resp = requests.get(wikipedia_url, params=params_search, headers=headers, timeout=5)
        data = resp.json()
        search_results = data.get("query", {}).get("search", [])
        if not search_results:
            return None

        page_title = search_results[0]["title"]

        # Get page image
        params_image = {
            "action": "query",
            "format": "json",
            "titles": page_title,
            "prop": "pageimages",
            "pithumbsize": 400
        }
        img_resp = requests.get(wikipedia_url, params=params_image, headers=headers, timeout=5)
        img_data = img_resp.json()
        pages = img_data.get("query", {}).get("pages", {})
        for page in pages.values():
            if page.get("thumbnail"):
                return {
                    "id": f"wiki_{page['pageid']}",
                    "url": page["thumbnail"]["source"],
                    "thumbnail": page["thumbnail"]["source"],
                    "date": "",
                    "source": "Wikipedia"
                }
    except Exception as e:
        logger.error(f"Wikipedia image fetch error: {e}")
    return None


def _fetch_wikimedia_geosearch(lat, lon, wikimedia_url, headers):
    """
    Fetch photos from Wikimedia Commons using geosearch around the given coordinates.
    Returns a list of photo dicts.
    """
    photos = []
    params_geo = {
        "action": "query",
        "format": "json",
        "list": "geosearch",
        "gscoord": f"{lat}|{lon}",
        "gsradius": "200",
        "gslimit": "10",
        "gsnamespace": "6"
    }
    try:
        geo_resp = requests.get(wikimedia_url, params=params_geo, headers=headers, timeout=8)
        geo_data = geo_resp.json()

        if "query" in geo_data and "geosearch" in geo_data["query"]:
            items = geo_data["query"]["geosearch"]

            # Fetch details in parallel
            with ThreadPoolExecutor(max_workers=5) as executor:
                future_to_item = {}
                for item in items:
                    title = item["title"]
                    if title.startswith("File:"):
                        title = title[5:]
                    params_info = {
                        "action": "query",
                        "format": "json",
                        "titles": f"File:{title}",
                        "prop": "imageinfo|categories",
                        "iiprop": "url|extmetadata",
                        "iiurlwidth": 400,
                        "cllimit": 10
                    }
                    future = executor.submit(requests.get, wikimedia_url, params=params_info, headers=headers, timeout=8)
                    future_to_item[future] = (item, title)

                for future in as_completed(future_to_item):
                    item, title = future_to_item[future]
                    try:
                        info_resp = future.result()
                        info_data = info_resp.json()
                        pages = info_data.get("query", {}).get("pages", {})
                        for page_id, page_info in pages.items():
                            if page_id == "-1":
                                continue
                            imageinfo = page_info.get("imageinfo", [])
                            if not imageinfo:
                                continue
                            categories = page_info.get("categories", [])
                            category_text = " ".join([cat.get("title", "") for cat in categories])
                            image_data = imageinfo[0]
                            extmetadata = image_data.get("extmetadata", {})
                            description = extmetadata.get("ImageDescription", {}).get("value", "")

                            if not _is_relevant_photo(title, description + " " + category_text):
                                continue

                            date = extmetadata.get("DateTimeOriginal", {}).get("value", "")
                            if not date:
                                date = extmetadata.get("DateTime", {}).get("value", "")
                            if date:
                                if "T" in date:
                                    date = date.split("T")[0]
                                elif len(date) > 10:
                                    date = date[:10]

                            photos.append({
                                "id": item.get("pageid"),
                                "url": image_data.get("url"),
                                "thumbnail": image_data.get("thumburl", image_data.get("url")),
                                "date": date,
                                "source": "Wikimedia Commons"
                            })
                    except Exception as e:
                        logger.error(f"Wikimedia detail fetch error for {title}: {e}")
    except Exception as e:
        logger.error(f"Wikimedia geosearch error: {e}")
    return photos


def _fetch_pic4carto(lat, lon, pic4carto_url, headers):
    """
    Fetch photos from Pic4Carto aggregator (Mapillary, Flickr, etc.).
    Returns a list of photo dicts.
    """
    photos = []
    params_pic = {
        "lat": lat,
        "lng": lon,
        "radius": 200,
        "limit": 10
    }
    try:
        resp = requests.get(pic4carto_url, params=params_pic, timeout=5)
        if resp.ok:
            data = resp.json()
            for item in data:
                title = item.get("title", "")
                description = item.get("description", "")
                if not _is_relevant_photo(title, description):
                    continue
                date_taken = item.get("date_taken", "")
                if date_taken and "T" in date_taken:
                    date_taken = date_taken.split("T")[0]
                photos.append({
                    "id": item.get("id"),
                    "url": item.get("url"),
                    "thumbnail": item.get("thumbnail_url", item.get("url")),
                    "date": date_taken,
                    "source": item.get("provider", "Pic4Carto")
                })
    except Exception as e:
        logger.warning(f"Pic4Carto error: {e}")  # warning, not error, to avoid noise
    return photos


def _fetch_wikipedia_description(lat, lon, name, wikipedia_url, headers):
    """
    Fetch a short Wikipedia description for the location.
    Tries by name first, then falls back to geosearch.
    """
    if name:
        params_search = {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": name,
            "srlimit": 1
        }
        try:
            search_resp = requests.get(wikipedia_url, params=params_search, headers=headers, timeout=5)
            search_data = search_resp.json()
            search_results = search_data.get("query", {}).get("search", [])
            if search_results:
                page_title = search_results[0]["title"]
                params_extract = {
                    "action": "query",
                    "format": "json",
                    "titles": page_title,
                    "prop": "extracts",
                    "exintro": True,
                    "explaintext": True,
                    "exsentences": 2
                }
                extract_resp = requests.get(wikipedia_url, params=params_extract, headers=headers, timeout=5)
                extract_data = extract_resp.json()
                pages = extract_data.get("query", {}).get("pages", {})
                for page in pages.values():
                    if page.get("pageid", -1) != -1:
                        return page.get("extract", "")
        except Exception as e:
            logger.error(f"Wikipedia name search error: {e}")

    # Fallback to geosearch
    try:
        params_geo = {
            "action": "query",
            "format": "json",
            "list": "geosearch",
            "gscoord": f"{lat}|{lon}",
            "gsradius": "500",
            "gslimit": "1"
        }
        geo_resp = requests.get(wikipedia_url, params=params_geo, headers=headers, timeout=5)
        geo_data = geo_resp.json()

        if "query" in geo_data and "geosearch" in geo_data["query"]:
            for item in geo_data["query"]["geosearch"]:
                page_title = item["title"]
                params_extract = {
                    "action": "query",
                    "format": "json",
                    "titles": page_title,
                    "prop": "extracts",
                    "exintro": True,
                    "explaintext": True,
                    "exsentences": 2
                }
                extract_resp = requests.get(wikipedia_url, params=params_extract, headers=headers, timeout=5)
                extract_data = extract_resp.json()
                pages = extract_data.get("query", {}).get("pages", {})
                for page in pages.values():
                    if page.get("pageid", -1) != -1:
                        return page.get("extract", "")
    except Exception as e:
        logger.error(f"Wikipedia geosearch error: {e}")
    return ""

def _compute_straight_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return an approximate straight-line distance in kilometres."""
    lat_diff = abs(lat1 - lat2) * 111          # 1 degree lat ≈ 111 km
    lon_diff = abs(lon1 - lon2) * 111 * 0.6    # rough lon correction
    return (lat_diff * 2 + lon_diff * 2) ** 0.5


def _check_route_ownership(route: Route, user) -> Response | None:
    """
    Return a 403 Response if user is neither the route owner nor staff,
    otherwise return None (meaning the check passed).
    """
    if route.owner != user and not user.is_staff:
        return Response(
            {"error": "Only the route owner can perform this action."},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


def _routing_services_unavailable() -> Response:
    return Response(
        {
            "error": (
                "Routing services not available. "
                "Please ensure the database is prepared."
            )
        },
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )
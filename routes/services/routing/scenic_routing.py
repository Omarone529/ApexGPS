import logging

from django.contrib.gis.geos import Point
from django.db import connection

from .base_routing import BaseRoutingService
from .utils import (
    _calculate_path_metrics,
    _create_route_geometry,
    _encode_linestring_to_polyline,
    _extract_edges_from_dijkstra_result,
    _find_nearest_vertex,
    _get_segments_by_ids,
    _validate_coordinates,
)

logger = logging.getLogger(__name__)

__all__ = ["ScenicRoutingService", "ScenicPreference"]


class ScenicPreference:
    """Container for scenic routing preferences."""

    def __init__(
        self,
        name: str,
        time_weight: float,
        scenic_weight: float,
        curvature_weight: float,
        max_time_increase_percent: float,
        description: str = "",
    ):
        """Initialize scenic preference."""
        self.name = name
        self.time_weight = time_weight
        self.scenic_weight = scenic_weight
        self.curvature_weight = curvature_weight
        self.max_time_increase_percent = max_time_increase_percent
        self.description = description

        # Normalize weights to sum to 1.0
        total = time_weight + scenic_weight + curvature_weight
        if total > 0:
            self.time_weight = time_weight / total
            self.scenic_weight = scenic_weight / total
            self.curvature_weight = curvature_weight / total


class ScenicRoutingService(BaseRoutingService):
    """
    Scenic routing service using real OSM data.

    Uses only existing database fields:
    - cost_time: Travel time in seconds
    - scenic_rating: Scenic quality (0-5, higher is better)
    - curvature: Road curvature (0-1, higher is more winding)

    Calculates scenic routes that balance travel time with scenic quality
    while respecting configurable time constraints.
    """

    # Predefined scenic preferences based on real data
    SCENIC_PREFERENCES = {
        "fast": ScenicPreference(
            name="fast",
            time_weight=0.8,
            scenic_weight=0.15,
            curvature_weight=0.05,
            max_time_increase_percent=10.0,
            description="Fastest scenic route, minimal time increase",
        ),
        "balanced": ScenicPreference(
            name="balanced",
            time_weight=0.5,
            scenic_weight=0.3,
            curvature_weight=0.2,
            max_time_increase_percent=20.0,
            description="Balanced mix of speed and scenery",
        ),
        "most_winding": ScenicPreference(
            name="most_winding",
            time_weight=0.3,
            scenic_weight=0.3,
            curvature_weight=0.4,
            max_time_increase_percent=30.0,
            description="Maximizes winding roads and scenery",
        ),
    }

    def __init__(self, preference: str = "balanced"):
        """Initialize scenic routing service."""
        if preference not in self.SCENIC_PREFERENCES:
            raise ValueError(
                f"Preference must be one of: {list(self.SCENIC_PREFERENCES.keys())}"
            )

        self.preference = preference
        self.preference_config = self.SCENIC_PREFERENCES[preference]

    def get_cost_column(self) -> str:
        """Get SQL expression for scenic cost calculation."""
        time_weight = self.preference_config.time_weight
        scenic_weight = self.preference_config.scenic_weight
        curvature_weight = self.preference_config.curvature_weight

        # Normalize cost_time (divide by 3600 to convert seconds to hours)
        # Lower time = better
        time_component = "(cost_time / 3600.0)"

        # Normalize scenic_rating: 0-5 scale, higher is better
        # Invert for cost: higher scenic rating = lower cost
        scenic_component = "(5.0 - COALESCE(scenic_rating, 2.5)) / 5.0"

        # Normalize curvature: 0-1 scale, higher is more winding
        # Invert for cost: more curvature = lower cost
        curvature_component = "(1.0 - COALESCE(curvature, 0.5))"

        # Combine with weights
        cost_expression = (
            f"({time_component} * {time_weight}) + "
            f"({scenic_component} * {scenic_weight}) + "
            f"({curvature_component} * {curvature_weight})"
        )

        return cost_expression

    def _calculate_scenic_metrics(self, segments: list[dict]) -> dict[str, float]:
        """Calculate scenic metrics from real segment data."""
        if not segments:
            return {
                "total_scenic_score": 0.0,
                "avg_scenic_rating": 0.0,
                "total_curvature": 0.0,
                "avg_curvature": 0.0,
                "weighted_scenic_score": 0.0,
                "segment_count": 0,
            }

        total_length = 0.0
        total_scenic = 0.0
        total_curvature = 0.0
        weighted_scenic = 0.0

        for segment in segments:
            length = segment.get("length_m", 0)
            scenic_rating = segment.get("scenic_rating", 0.0)
            curvature = segment.get("curvature", 0.0)

            # Use default values if None
            if scenic_rating is None:
                scenic_rating = 2.5
            if curvature is None:
                curvature = 0.5
            total_length += length
            total_scenic += scenic_rating * length
            total_curvature += curvature * length
            weighted_scenic += (scenic_rating * curvature) * length

        # Calculate averages weighted by segment length
        avg_scenic = total_scenic / total_length if total_length > 0 else 0.0
        avg_curvature = total_curvature / total_length if total_length > 0 else 0.0

        # Calculate overall scenic score (0-100 scale)
        # Based on real scenic_rating (0-5) and curvature (0-1)
        scenic_score = (avg_scenic / 5.0 * 70) + (avg_curvature * 30)
        scenic_score = min(100.0, max(0.0, scenic_score))

        # Calculate weighted scenic score
        weighted_avg_scenic = (
            weighted_scenic / total_length if total_length > 0 else 0.0
        )
        weighted_scenic_score = weighted_avg_scenic / 5.0 * 100

        return {
            "total_scenic_score": scenic_score,
            "weighted_scenic_score": weighted_scenic_score,
            "avg_scenic_rating": avg_scenic,
            "total_curvature": total_curvature,
            "avg_curvature": avg_curvature,
            "segment_count": len(segments),
            "total_length_m": total_length,
        }

    def _execute_scenic_dijkstra_query(
        self, start_vertex: int, end_vertex: int
    ) -> list[tuple]:
        """Execute Dijkstra algorithm with scenic cost function."""
        cost_expression = self.get_cost_column()

        with connection.cursor() as cursor:
            query = f"""
            SELECT seq, path_seq, node, edge, cost, agg_cost
            FROM pgr_dijkstra(
                'SELECT
                    id,
                    source,
                    target,
                    {cost_expression} as cost,
                    {cost_expression} as reverse_cost
                 FROM gis_data_roadsegment
                 WHERE geometry IS NOT NULL
                 AND source IS NOT NULL
                 AND target IS NOT NULL
                 AND is_active = true
                 AND highway NOT IN
                 (''footway'', ''path'', ''cycleway'', ''steps'', ''service'')',
                %s, %s, directed := true
            )
            ORDER BY seq
            """
            cursor.execute(query, [start_vertex, end_vertex])
            return cursor.fetchall()

    def _find_scenic_alternative_routes(
        self, start_vertex: int, end_vertex: int, max_routes: int = 5
    ) -> list[tuple[list[int], float]]:
        """Find alternative scenic routes using Yen's k-shortest paths."""
        cost_expression = self.get_cost_column()

        try:
            with connection.cursor() as cursor:
                query = f"""
                WITH paths AS (
                    SELECT (pgr_ksp(
                        'SELECT
                            id,
                            source,
                            target,
                            {cost_expression} as cost,
                            {cost_expression} as reverse_cost
                         FROM gis_data_roadsegment
                         WHERE geometry IS NOT NULL
                         AND source IS NOT NULL
                         AND target IS NOT NULL
                         AND is_active = true
                         AND highway NOT IN
                         (''footway'', ''path'', ''cycleway'', ''steps'', ''service'')',
                        %s, %s, %s, directed := true
                    )).*
                )
                SELECT path_id, edge, cost
                FROM paths
                WHERE edge != -1
                ORDER BY path_id, seq
                """

                cursor.execute(query, [start_vertex, end_vertex, max_routes])
                results = cursor.fetchall()

                # Group edges by path_id
                routes = {}
                for path_id, edge, cost in results:
                    if path_id not in routes:
                        routes[path_id] = {"edges": [], "cost": 0.0}
                    routes[path_id]["edges"].append(edge)
                    routes[path_id]["cost"] += cost

                # Convert to list of tuples
                return [(data["edges"], data["cost"]) for data in routes.values()]

        except Exception as e:
            logger.error(f"Error finding alternative routes: {str(e)}")
            return []

    def calculate_route(
        self, start_point: Point, end_point: Point, **kwargs
    ) -> dict | None:
        """Calculate scenic route between two points using real data."""
        vertex_threshold = kwargs.get("vertex_threshold", self.DEFAULT_VERTEX_THRESHOLD)
        reference_fastest_time = kwargs.get("reference_fastest_time")
        find_alternatives = kwargs.get("find_alternatives", False)
        max_alternatives = kwargs.get("max_alternatives", 3)

        # Find nearest vertices on road network
        start_vertex = _find_nearest_vertex(start_point, vertex_threshold)
        end_vertex = _find_nearest_vertex(end_point, vertex_threshold)

        if not start_vertex or not end_vertex:
            logger.warning("Cannot find nearest vertices for start or end point")
            return None

        try:
            if find_alternatives:
                # Find multiple scenic routes and choose best one
                alternative_routes = self._find_scenic_alternative_routes(
                    start_vertex=start_vertex,
                    end_vertex=end_vertex,
                    max_routes=max_alternatives,
                )

                if not alternative_routes:
                    logger.warning("No alternative scenic routes found")
                    return None

                # Get segments for each route and calculate metrics
                best_route = None
                best_scenic_score = -1.0

                for edge_ids, route_cost in alternative_routes:
                    segments = _get_segments_by_ids(edge_ids)
                    if not segments:
                        continue

                    # Calculate scenic metrics
                    scenic_metrics = self._calculate_scenic_metrics(segments)
                    scenic_score = scenic_metrics["weighted_scenic_score"]

                    # Check time constraint if reference is provided
                    if reference_fastest_time:
                        route_metrics = _calculate_path_metrics(segments)
                        route_time = route_metrics["total_time_seconds"]
                        time_increase = (
                            (route_time - reference_fastest_time)
                            / reference_fastest_time
                        ) * 100.0

                        if (
                            time_increase
                            > self.preference_config.max_time_increase_percent
                        ):
                            logger.debug(
                                f"Route exceeds time constraint: {time_increase:.1f}%"
                            )
                            continue

                    # Track best route by scenic score
                    if scenic_score > best_scenic_score:
                        best_scenic_score = scenic_score
                        best_route = (edge_ids, segments, route_cost)

                if not best_route:
                    logger.warning("No scenic routes within constraints")
                    return None

                edge_ids, segments, route_cost = best_route

            else:
                # Use direct Dijkstra with scenic cost
                dijkstra_result = self._execute_scenic_dijkstra_query(
                    start_vertex, end_vertex
                )

                if not dijkstra_result:
                    logger.warning("No Dijkstra result for scenic route")
                    return None

                edge_ids = _extract_edges_from_dijkstra_result(dijkstra_result)

                if not edge_ids:
                    logger.warning("No edges found in Dijkstra result")
                    return None

                segments = _get_segments_by_ids(edge_ids)

                if not segments:
                    logger.warning("Cannot retrieve segments by IDs")
                    return None

            # Calculate route metrics
            route_metrics = _calculate_path_metrics(segments)
            scenic_metrics = self._calculate_scenic_metrics(segments)

            # Create route geometry
            route_geometry = _create_route_geometry(segments)
            polyline_encoded = _encode_linestring_to_polyline(route_geometry)

            # Calculate time increase percentage if reference is provided
            time_increase_percentage = 0.0
            if reference_fastest_time and route_metrics["total_time_seconds"] > 0:
                time_increase_percentage = (
                    (route_metrics["total_time_seconds"] - reference_fastest_time)
                    / reference_fastest_time
                    * 100.0
                )

            is_within_constraint = (
                time_increase_percentage
                <= self.preference_config.max_time_increase_percent
            )

            result = {
                "route_type": "scenic",
                "preference": self.preference,
                "preference_name": self.preference_config.name,
                "preference_description": self.preference_config.description,
                "start_vertex": start_vertex,
                "end_vertex": end_vertex,
                **route_metrics,
                **scenic_metrics,
                "polyline": polyline_encoded,
                "geometry": route_geometry,
                "segments": segments[:10],
                "total_segments": len(segments),
                "time_constraint": {
                    "max_increase_percent": self.preference_config.max_time_increase_percent,  # noqa:E501
                    "actual_increase_percent": round(time_increase_percentage, 1),
                    "is_within_constraint": is_within_constraint,
                    "reference_fastest_time": reference_fastest_time,
                },
                "cost_weights": {
                    "time_weight": round(self.preference_config.time_weight, 3),
                    "scenic_weight": round(self.preference_config.scenic_weight, 3),
                    "curvature_weight": round(
                        self.preference_config.curvature_weight, 3
                    ),
                },
            }

            return result

        except Exception as e:
            logger.error(f"Error calculating scenic route: {str(e)}")
            return None

    def calculate_scenic_route(
        self,
        start_lat: float,
        start_lon: float,
        end_lat: float,
        end_lon: float,
        reference_fastest_time: float | None = None,
        **kwargs,
    ) -> dict | None:
        """Calculate scenic route from coordinates."""
        # Validate coordinates
        for coord_name, lat, lon in [
            ("start", start_lat, start_lon),
            ("end", end_lat, end_lon),
        ]:
            is_valid, error_msg = _validate_coordinates(lat, lon)
            if not is_valid:
                raise ValueError(f"Invalid {coord_name} coordinates: {error_msg}")

        start_point = Point(start_lon, start_lat, srid=4326)
        end_point = Point(end_lon, end_lat, srid=4326)

        # Calculate route
        return self.calculate_route(
            start_point=start_point,
            end_point=end_point,
            reference_fastest_time=reference_fastest_time,
            **kwargs,
        )

    def calculate_scenic_route_with_fastest_reference(
        self,
        start_lat: float,
        start_lon: float,
        end_lat: float,
        end_lon: float,
        **kwargs,
    ) -> dict:
        """Calculate scenic route with automatic fastest route reference."""
        from .fast_routing import FastRoutingService

        # First calculate fastest route for reference
        fast_service = FastRoutingService()
        fastest_route = fast_service.calculate_fastest_route(
            start_lat=start_lat,
            start_lon=start_lon,
            end_lat=end_lat,
            end_lon=end_lon,
            **kwargs,
        )

        if not fastest_route:
            return {
                "success": False,
                "error": "Cannot calculate fastest route for reference",
            }

        fastest_time = fastest_route.get("total_time_seconds", 0)

        # Calculate scenic route with reference
        scenic_route = self.calculate_scenic_route(
            start_lat=start_lat,
            start_lon=start_lon,
            end_lat=end_lat,
            end_lon=end_lon,
            reference_fastest_time=fastest_time,
            find_alternatives=True,
            **kwargs,
        )

        if not scenic_route:
            return {
                "success": False,
                "error": "Cannot calculate scenic route",
                "fastest_route": fastest_route,
            }

        # Calculate time difference
        scenic_time = scenic_route.get("total_time_seconds", 0)
        time_difference = scenic_time - fastest_time
        time_difference_percent = (
            (time_difference / fastest_time * 100) if fastest_time > 0 else 0
        )

        return {
            "success": True,
            "fastest_route": {
                "total_time_seconds": fastest_time,
                "total_distance_km": fastest_route.get("total_distance_km", 0),
                "total_time_minutes": fastest_route.get("total_time_minutes", 0),
            },
            "scenic_route": scenic_route,
            "comparison": {
                "time_difference_seconds": round(time_difference, 1),
                "time_difference_minutes": round(time_difference / 60, 1),
                "time_difference_percent": round(time_difference_percent, 1),
                "is_within_constraint": time_difference_percent
                <= self.preference_config.max_time_increase_percent,
                "constraint_limit_percent": self.preference_config.max_time_increase_percent,  # noqa:E501
            },
        }

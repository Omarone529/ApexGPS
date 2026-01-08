from django.contrib.gis.geos import Point

from .base_routing import BaseRoutingService
from .utils import (
    _calculate_path_metrics,
    _create_route_geometry,
    _encode_linestring_to_polyline,
    _execute_dijkstra_query,
    _extract_edges_from_dijkstra_result,
    _find_nearest_vertex,
    _get_segments_by_ids,
    _validate_coordinates,
)

__all__ = ["FastRoutingService"]


class FastRoutingService(BaseRoutingService):
    """
    Fast routing service using Dijkstra's algorithm.
    Uses cost_time column to find the fastest route between two points.
    """

    def get_cost_column(self) -> str:
        """Get cost column for fastest routing (time-based)."""
        return "cost_time"

    def calculate_route(
        self, start_point: Point, end_point: Point, **kwargs
    ) -> dict | None:
        """Calculate fastest route between two points."""
        vertex_threshold = kwargs.get("vertex_threshold", self.DEFAULT_VERTEX_THRESHOLD)

        # Find nearest vertices on road network
        start_vertex = _find_nearest_vertex(start_point, vertex_threshold)
        end_vertex = _find_nearest_vertex(end_point, vertex_threshold)

        if not start_vertex or not end_vertex:
            return None

        # Execute Dijkstra algorithm
        dijkstra_result = _execute_dijkstra_query(
            start_vertex, end_vertex, self.get_cost_column()
        )

        if not dijkstra_result:
            return None
        edge_ids = _extract_edges_from_dijkstra_result(dijkstra_result)

        if not edge_ids:
            return None
        segments = _get_segments_by_ids(edge_ids)

        if not segments:
            return None

        # Create route geometry
        route_geometry = _create_route_geometry(segments)
        metrics = _calculate_path_metrics(segments)
        polyline_encoded = _encode_linestring_to_polyline(route_geometry)
        return {
            "route_type": "fastest",
            "preference": "fast",
            "start_vertex": start_vertex,
            "end_vertex": end_vertex,
            "vertex_count": len(dijkstra_result),
            **metrics,
            "polyline": polyline_encoded,
            "geometry": route_geometry,
            "segments": segments[:10],
            "total_segments": len(segments),
        }

    def calculate_fastest_route(
        self,
        start_lat: float,
        start_lon: float,
        end_lat: float,
        end_lon: float,
        **kwargs,
    ) -> dict | None:
        """Calculate route from coordinates."""
        # Validate coordinates
        for coord_name, lat, lon in [
            ("start", start_lat, start_lon),
            ("end", end_lat, end_lon),
        ]:
            is_valid, error_msg = _validate_coordinates(lat, lon)
            if not is_valid:
                raise ValueError(f"Invalid {coord_name} coordinates: {error_msg}")

        # Create points
        start_point = Point(start_lon, start_lat)
        end_point = Point(end_lon, end_lat)

        # Calculate route
        return self.calculate_route(start_point, end_point, **kwargs)

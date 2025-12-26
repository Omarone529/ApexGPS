import polyline
from django.contrib.gis.geos import Point
from django.db import connection

from .base_routing import BaseRoutingService

__all__ = ["FastRoutingService"]


class FastRoutingService(BaseRoutingService):
    """
    Fast routing service using Dijkstra's algorithm.

    Uses cost_time column to find the fastest route between two points.
    This route serves as the baseline for time constraints on scenic routes.
    """

    def get_cost_column(self) -> str:
        """Get cost column for fastest routing (time-based)."""
        return "cost_time"

    def _calculate_dijkstra_path(
        self, start_vertex: int, end_vertex: int
    ) -> list[tuple]:
        """Calculate path using Dijkstra's algorithm."""
        cost_column = self.get_cost_column()

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

    def _extract_vertex_ids(self, dijkstra_result: list[tuple]) -> list[int]:
        """Extract vertex IDs from Dijkstra result."""
        if not dijkstra_result:
            return []

        # pgr_dijkstra returns: (seq, path_seq, node, edge, cost, agg_cost)
        # need the node column (index 2)
        return [row[2] for row in dijkstra_result]

    def _get_path_segments(self, vertex_ids: list[int]) -> list[dict]:
        """Get road segments for a list of vertex IDs."""
        if len(vertex_ids) < 2:
            return []

        segments = []
        for i in range(len(vertex_ids) - 1):
            segment = self.get_road_segment(vertex_ids[i], vertex_ids[i + 1])
            if segment:
                segments.append(segment)

        return segments

    @staticmethod
    def _encode_path_to_polyline(segments: list[dict]) -> str:
        """Convert path segments to encoded polyline."""
        coordinates = []
        for seg in segments:
            if seg["geometry"]:
                # geometry.coords returns (lon, lat) tuples
                coords = list(seg["geometry"].coords)
                coordinates.extend(coords)

        if not coordinates:
            return ""

        # Remove duplicates while preserving order
        unique_coords = []
        seen = set()
        for coord in coordinates:
            if coord not in seen:
                seen.add(coord)
                unique_coords.append(coord)

        # polyline.encode expects (lat, lon) tuples
        lat_lon_coords = [(lat, lon) for lon, lat in unique_coords]
        return polyline.encode(lat_lon_coords)

    def calculate_route(
        self, start_point: Point, end_point: Point, **kwargs
    ) -> dict | None:
        """Calculate fastest route between two points."""
        # Get vertex threshold from kwargs or use default
        vertex_threshold = kwargs.get("vertex_threshold", self.DEFAULT_VERTEX_THRESHOLD)

        # Find nearest vertices
        start_vertex = self.find_nearest_vertex(start_point, vertex_threshold)
        end_vertex = self.find_nearest_vertex(end_point, vertex_threshold)

        if not start_vertex or not end_vertex:
            return None

        # Dijkstra path
        dijkstra_result = self._calculate_dijkstra_path(start_vertex, end_vertex)
        if not dijkstra_result:
            return None

        # Extract vertex IDs and get segments
        vertex_ids = self._extract_vertex_ids(dijkstra_result)
        segments = self._get_path_segments(vertex_ids)

        if not segments:
            return None

        # Calculate metrics
        metrics = self.calculate_path_metrics(segments)

        # Encode polyline
        polyline_encoded = self._encode_path_to_polyline(segments)

        # Compile final result
        return {
            "route_type": "fastest",
            "preference": "fast",
            "start_vertex": start_vertex,
            "end_vertex": end_vertex,
            "vertex_count": len(vertex_ids),
            **metrics,
            "polyline": polyline_encoded,
            "segments": segments[:10],  # Return first 10 segments for debugging
        }

    def calculate_fastest_route(
        self,
        start_lat: float,
        start_lon: float,
        end_lat: float,
        end_lon: float,
        **kwargs,
    ) -> dict | None:
        """Method to calculate route from coordinates."""
        for coord_name, lat, lon in [
            ("start", start_lat, start_lon),
            ("end", end_lat, end_lon),
        ]:
            is_valid, error_msg = self.validate_coordinates(lat, lon)
            if not is_valid:
                raise ValueError(f"Invalid {coord_name} coordinates: {error_msg}")

        # Create points
        start_point = Point(start_lon, start_lat)
        end_point = Point(end_lon, end_lat)

        # Calculate route
        return self.calculate_route(start_point, end_point, **kwargs)

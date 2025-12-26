from abc import ABC, abstractmethod

from django.contrib.gis.geos import Point
from django.db import connection

__all__ = ["BaseRoutingService"]


class BaseRoutingService(ABC):
    """Abstract base class for all routing services."""

    # Default distance threshold for vertex snapping (approx 1km)
    DEFAULT_VERTEX_THRESHOLD = 0.01  # degrees

    @staticmethod
    def validate_coordinates(lat: float, lon: float) -> tuple[bool, str]:
        """Validate geographic coordinates."""
        if not (-90 <= lat <= 90):
            return False, f"Latitude {lat} is out of valid range (-90 to 90)"

        if not (-180 <= lon <= 180):
            return False, f"Longitude {lon} is out of valid range (-180 to 180)"

        return True, ""

    @staticmethod
    def find_nearest_vertex(
        point: Point, distance_threshold: float = DEFAULT_VERTEX_THRESHOLD
    ) -> int | None:
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

    @staticmethod
    def get_road_segment(source_vertex: int, target_vertex: int) -> dict | None:
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

    @staticmethod
    def calculate_path_metrics(segments: list[dict]) -> dict:
        """Calculate total metrics for a list of road segments."""
        if not segments:
            return {
                "total_distance_m": 0.0,
                "total_distance_km": 0.0,
                "total_time_seconds": 0.0,
                "total_time_minutes": 0.0,
                "segment_count": 0,
            }

        total_distance_m = sum(seg["length_m"] for seg in segments)
        total_time_seconds = sum(seg["cost_time"] for seg in segments)

        return {
            "total_distance_m": total_distance_m,
            "total_distance_km": total_distance_m / 1000,
            "total_time_seconds": total_time_seconds,
            "total_time_minutes": total_time_seconds / 60,
            "segment_count": len(segments),
        }

    @abstractmethod
    def calculate_route(
        self, start_point: Point, end_point: Point, **kwargs
    ) -> dict | None:
        """Abstract method to calculate route between two points."""

    @abstractmethod
    def get_cost_column(self) -> str:
        """Get the cost column to use for this routing algorithm."""

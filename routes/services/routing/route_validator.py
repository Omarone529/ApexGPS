from django.contrib.gis.geos import Point
from django.db import connection

from .utils import (
    _find_nearest_vertex,
    _validate_coordinates,
)

__all__ = ["RouteValidator"]


class RouteValidator:
    """Service for validating routing inputs and results."""

    @staticmethod
    def check_vertex_connectivity(vertex_id: int) -> bool:
        """Check if a vertex has outgoing or incoming edges."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM gis_data_roadsegment
                    WHERE (source = %s OR target = %s)
                    AND is_active = true
                )
                """,
                [vertex_id, vertex_id],
            )
            return cursor.fetchone()[0]

    def validate_route_distance(
        start_point: Point, end_point: Point, max_distance_km: float = 1000.0
    ) -> tuple[bool, str]:
        """Validate that route distance is reasonable."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT ST_Distance(%s::geography, %s::geography) / 1000.0
                """,
                [start_point, end_point],
            )
            straight_line_km = cursor.fetchone()[0]

            if straight_line_km > max_distance_km:
                return False, (
                    f"Straight-line distance ({straight_line_km:.1f} km) "
                    f"exceeds maximum ({max_distance_km} km)"
                )
            return True, f"Distance: {straight_line_km:.1f} km"

    @staticmethod
    def get_network_coverage_bounds() -> dict | None:
        """Get bounding box of the road network."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    ST_YMin(ST_Extent(the_geom)) as min_lat,
                    ST_YMax(ST_Extent(the_geom)) as max_lat,
                    ST_XMin(ST_Extent(the_geom)) as min_lon,
                    ST_XMax(ST_Extent(the_geom)) as max_lon
                FROM gis_data_roadsegment_vertices_pgr
                """
            )
            result = cursor.fetchone()

            if not result or None in result:
                return None

            return {
                "min_lat": result[0],
                "max_lat": result[1],
                "min_lon": result[2],
                "max_lon": result[3],
            }

    @staticmethod
    def is_point_in_network_bounds(point: Point) -> tuple[bool, str]:
        """Check if point is within road network coverage."""
        bounds = RouteValidator.get_network_coverage_bounds()
        if not bounds:
            return False, "Cannot determine network bounds"

        lat, lon = point.y, point.x

        if not (bounds["min_lat"] <= lat <= bounds["max_lat"]):
            return False, (
                f"Latitude {lat} outside bounds "
                f"({bounds['min_lat']} to {bounds['max_lat']})"
            )

        if not (bounds["min_lon"] <= lon <= bounds["max_lon"]):
            return False, (
                f"Longitude {lon} outside bounds "
                f"({bounds['min_lon']} to {bounds['max_lon']})"
            )

        return True, "Within network bounds"

    def full_route_validation(
        self,
        start_lat: float,
        start_lon: float,
        end_lat: float,
        end_lon: float,
        max_distance_km: float = 1000.0,
    ) -> dict:
        """Perform complete validation of routing request."""
        results = {
            "is_valid": False,
            "errors": [],
            "warnings": [],
            "start_vertex": None,
            "end_vertex": None,
        }

        for coord_name, lat, lon in [
            ("start", start_lat, start_lon),
            ("end", end_lat, end_lon),
        ]:
            is_valid, error_msg = _validate_coordinates(lat, lon)
            if not is_valid:
                results["errors"].append(f"{coord_name}: {error_msg}")

        if results["errors"]:
            return results

        start_point = Point(start_lon, start_lat, srid=4326)
        end_point = Point(end_lon, end_lat, srid=4326)

        # Check network bounds
        in_bounds, bounds_msg = self.is_point_in_network_bounds(start_point)
        if not in_bounds:
            results["warnings"].append(f"Start: {bounds_msg}")

        in_bounds, bounds_msg = self.is_point_in_network_bounds(end_point)
        if not in_bounds:
            results["warnings"].append(f"End: {bounds_msg}")

        # Find vertices and check connectivity
        start_vertex = _find_nearest_vertex(start_point)
        end_vertex = _find_nearest_vertex(end_point)

        results["start_vertex"] = start_vertex
        results["end_vertex"] = end_vertex

        if start_vertex:
            if not self.check_vertex_connectivity(start_vertex):
                results["errors"].append("Start point not connected to road network")
        else:
            results["errors"].append("Cannot find start point on road network")

        if end_vertex:
            if not self.check_vertex_connectivity(end_vertex):
                results["errors"].append("End point not connected to road network")
        else:
            results["errors"].append("Cannot find end point on road network")

        # Validate distance
        distance_valid, distance_msg = self.validate_route_distance(
            start_point, end_point, max_distance_km
        )

        if not distance_valid:
            results["errors"].append(distance_msg)
        else:
            results["warnings"].append(distance_msg)

        # Final validation result
        results["is_valid"] = len(results["errors"]) == 0

        return results

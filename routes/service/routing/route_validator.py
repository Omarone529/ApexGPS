from django.contrib.gis.geos import Point
from django.db import connection

__all__ = ["RouteValidator"]


class RouteValidator:
    """Service for validating routing inputs and results."""

    @staticmethod
    def validate_bounding_box(
        min_lat: float, max_lat: float, min_lon: float, max_lon: float
    ) -> tuple[bool, str]:
        """Validate geographic bounding box."""
        if min_lat >= max_lat:
            return False, "min_lat must be less than max_lat"

        if min_lon >= max_lon:
            return False, "min_lon must be less than max_lon"

        if not (-90 <= min_lat <= 90):
            return False, f"min_lat {min_lat} out of range (-90 to 90)"

        if not (-90 <= max_lat <= 90):
            return False, f"max_lat {max_lat} out of range (-90 to 90)"

        if not (-180 <= min_lon <= 180):
            return False, f"min_lon {min_lon} out of range (-180 to 180)"

        if not (-180 <= max_lon <= 180):
            return False, f"max_lon {max_lon} out of range (-180 to 180)"

        return True, ""

    @staticmethod
    def check_point_connectivity(point: Point) -> tuple[bool, int | None]:
        """Check if a point is connected to the road network."""
        lon, lat = point.x, point.y

        with connection.cursor() as cursor:
            # Find nearest vertex
            cursor.execute(
                """
                SELECT id
                FROM gis_data_roadsegment_vertices_pgr
                ORDER BY ST_Distance(the_geom, ST_MakePoint(%s, %s))
                LIMIT 1
                """,
                [lon, lat],
            )

            result = cursor.fetchone()
            if not result:
                return False, None

            vertex_id = result[0]

            # Check if vertex has outgoing or incoming edges
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

            is_connected = cursor.fetchone()[0]
            return is_connected, vertex_id

    @staticmethod
    def validate_route_distance(
        start_point: Point, end_point: Point, max_distance_km: float = 1000.0
    ) -> tuple[bool, str]:
        """Validate that route distance is reasonable."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT ST_Distance(
                    ST_MakePoint(%s, %s)::geography,
                    ST_MakePoint(%s, %s)::geography
                ) / 1000.0
                """,
                [start_point.x, start_point.y, end_point.x, end_point.y],
            )

            straight_line_km = cursor.fetchone()[0]

            if straight_line_km > max_distance_km:
                return False, (
                    f"Straight-line distance ({straight_line_km:.1f} km) "
                    f"exceeds maximum allowed ({max_distance_km} km)"
                )

            return True, f"Distance OK: {straight_line_km:.1f} km"

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
    def is_point_in_network_bounds(point: Point) -> tuple[bool, str | None]:
        """Check if point is within road network coverage area."""
        bounds = RouteValidator.get_network_coverage_bounds()
        if not bounds:
            return False, "Cannot determine network bounds"

        lat, lon = point.y, point.x

        if not (bounds["min_lat"] <= lat <= bounds["max_lat"]):
            return False, (
                f"Latitude {lat} is outside network bounds "
                f"({bounds['min_lat']} to {bounds['max_lat']})"
            )

        if not (bounds["min_lon"] <= lon <= bounds["max_lon"]):
            return False, (
                f"Longitude {lon} is outside network bounds "
                f"({bounds['min_lon']} to {bounds['max_lon']})"
            )

        return True, "Point is within network bounds"

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

        # Basic coordinate validation
        from .base_routing import BaseRoutingService

        for coord_name, lat, lon in [
            ("start", start_lat, start_lon),
            ("end", end_lat, end_lon),
        ]:
            is_valid, error_msg = BaseRoutingService.validate_coordinates(lat, lon)
            if not is_valid:
                results["errors"].append(f"{coord_name}: {error_msg}")

        if results["errors"]:
            return results

        # Create points
        start_point = Point(start_lon, start_lat)
        end_point = Point(end_lon, end_lat)

        # Check network bounds
        in_bounds, bounds_msg = self.is_point_in_network_bounds(start_point)
        if not in_bounds:
            results["warnings"].append(f"Start point: {bounds_msg}")

        in_bounds, bounds_msg = self.is_point_in_network_bounds(end_point)
        if not in_bounds:
            results["warnings"].append(f"End point: {bounds_msg}")

        # Check connectivity
        start_connected, start_vertex = self.check_point_connectivity(start_point)
        end_connected, end_vertex = self.check_point_connectivity(end_point)

        if not start_connected:
            results["errors"].append("Start point is not connected to road network")

        if not end_connected:
            results["errors"].append("End point is not connected to road network")

        results["start_vertex"] = start_vertex
        results["end_vertex"] = end_vertex

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

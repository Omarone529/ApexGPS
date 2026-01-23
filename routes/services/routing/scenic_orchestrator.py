import logging
import time

from django.contrib.gis.geos import Point

from .fast_routing import FastRoutingService
from .scenic_routing import ScenicRoutingService
from .utils import (
    _calculate_path_metrics,
    _create_route_geometry,
    _encode_linestring_to_polyline,
    _get_segments_by_ids,
    _validate_coordinates,
)

logger = logging.getLogger(__name__)

__all__ = ["ScenicRouteOrchestrator"]


class ScenicRouteOrchestrator:
    """Orchestrates scenic route calculation with time constraints."""

    @staticmethod
    def _get_route_segments_with_metrics(edge_ids: list[int]) -> dict | None:
        """Get route segments and calculate metrics."""
        segments = _get_segments_by_ids(edge_ids)
        if not segments:
            return None

        metrics = _calculate_path_metrics(segments)
        geometry = _create_route_geometry(segments)
        polyline = _encode_linestring_to_polyline(geometry)

        return {
            "segments": segments,
            "metrics": metrics,
            "geometry": geometry,
            "polyline": polyline,
            "segment_count": len(segments),
        }

    @staticmethod
    def _calculate_scenic_score_for_segments(segments: list[dict]) -> float:
        """Calculate scenic score for segments."""
        if not segments:
            return 0.0

        total_length = 0.0
        total_scenic = 0.0

        for segment in segments:
            length = segment.get("length_m", 0)
            scenic_rating = segment.get("scenic_rating", 0.0)
            curvature = segment.get("curvature", 0.0)

            # Use defaults for None values
            if scenic_rating is None:
                scenic_rating = 2.5
            if curvature is None:
                curvature = 0.5

            # Weight scenic rating by curvature
            # Curvy roads with high scenic rating are best
            weighted_scenic = scenic_rating * (1.0 + curvature)

            total_length += length
            total_scenic += weighted_scenic * length

        if total_length == 0:
            return 0.0

        avg_weighted_scenic = total_scenic / total_length
        # Convert to 0-100 scale (scenic_rating is 0-5)
        scenic_score = (avg_weighted_scenic / 5.0) * 100

        return min(100.0, max(0.0, scenic_score))

    @staticmethod
    def find_best_scenic_route_with_constraint(
        start_point: Point,
        end_point: Point,
        preference: str = "balanced",
        vertex_threshold: float = 0.01,
    ) -> dict:
        """Find best scenic route that respects time constraint."""
        start_time = time.time()

        # Calculate fastest route for baseline
        logger.info("Calculating fastest route for time reference...")
        fast_service = FastRoutingService()
        fastest_result = fast_service.calculate_route(
            start_point=start_point,
            end_point=end_point,
            vertex_threshold=vertex_threshold,
        )

        if not fastest_result:
            return {
                "success": False,
                "error": "Cannot calculate fastest route",
                "processing_time_ms": 0,
            }

        fastest_time = fastest_result.get("total_time_seconds", 0)
        fastest_minutes = fastest_result.get("total_time_minutes", 0)
        logger.info(f"Fastest route time: {fastest_minutes:.1f} min")

        # Calculate scenic route
        logger.info(f"Calculating scenic route with '{preference}' preference...")
        scenic_service = ScenicRoutingService(preference=preference)

        scenic_result = scenic_service.calculate_route(
            start_point=start_point,
            end_point=end_point,
            reference_fastest_time=fastest_minutes,
            vertex_threshold=vertex_threshold,
        )

        processing_time_ms = (time.time() - start_time) * 1000

        if not scenic_result:
            return {
                "success": False,
                "error": "Cannot calculate scenic route",
                "fastest_route": fastest_result,
                "processing_time_ms": round(processing_time_ms, 2),
            }

        # Calculate comparison metrics
        scenic_minutes = scenic_result.get("total_time_minutes", 0)
        time_excess_minutes = scenic_minutes - fastest_minutes
        time_excess_percent = (
            (time_excess_minutes / fastest_minutes * 100) if fastest_minutes > 0 else 0
        )
        actual_scenic_score = scenic_result.get("total_scenic_score", 0)
        max_excess_minutes = ScenicRoutingService.MAX_TIME_EXCESS_MINUTES
        is_within_constraint = time_excess_minutes <= max_excess_minutes

        result = {
            "success": True,
            "calculation": {
                "preference": preference,
                "preference_description": scenic_service.config["description"],
                "is_within_time_constraint": is_within_constraint,
                "constraint_limit_minutes": max_excess_minutes,
                "processing_time_ms": round(processing_time_ms, 2),
            },
            "fastest_route": {
                "total_time_seconds": fastest_time,
                "total_time_minutes": fastest_minutes,
                "total_distance_km": fastest_result.get("total_distance_km", 0),
                "polyline": fastest_result.get("polyline", ""),
                "segment_count": fastest_result.get("segment_count", 0),
            },
            "scenic_route": {
                "total_time_seconds": scenic_result.get("total_time_seconds", 0),
                "total_time_minutes": scenic_minutes,
                "total_distance_km": scenic_result.get("total_distance_km", 0),
                "scenic_score": actual_scenic_score,
                "avg_scenic_rating": scenic_result.get("avg_scenic_rating", 0),
                "avg_curvature": scenic_result.get("avg_curvature", 0),
                "total_poi_density": scenic_result.get("total_poi_density", 0),
                "polyline": scenic_result.get("polyline", ""),
                "segment_count": scenic_result.get("segment_count", 0),
                "poi_count": scenic_result.get("poi_count", 0),
                "poi_stops": scenic_result.get("poi_stops", []),
                "time_constraint": scenic_result.get("time_constraint", {}),
            },
            "comparison": {
                "time_excess_minutes": round(time_excess_minutes, 1),
                "time_excess_percent": round(time_excess_percent, 1),
                "scenic_score": round(actual_scenic_score, 1),
                "scenic_score_difference": round(
                    actual_scenic_score - 50, 1
                ),  # vs average 50
                "poi_count": scenic_result.get("poi_count", 0),
                "recommendation": "scenic"
                if is_within_constraint and actual_scenic_score > 60
                else "fastest",
            },
        }

        logger.info(
            f"Scenic route calculation complete: "
            f"time +{time_excess_minutes:.1f}min (+{time_excess_percent:.1f}%), "
            f"scenic score: {actual_scenic_score:.1f}/100, "
            f"POIs: {scenic_result.get('poi_count', 0)}, "
            f"constraint: {'OK' if is_within_constraint else 'EXCEEDED'}"
        )

        return result

    @staticmethod
    def calculate_from_coordinates(
        start_lat: float,
        start_lon: float,
        end_lat: float,
        end_lon: float,
        preference: str = "balanced",
        **kwargs,
    ) -> dict:
        """Calculate scenic route from coordinates."""
        # Validate coordinates
        for coord_name, lat, lon in [
            ("start", start_lat, start_lon),
            ("end", end_lat, end_lon),
        ]:
            is_valid, error_msg = _validate_coordinates(lat, lon)
            if not is_valid:
                return {
                    "success": False,
                    "error": f"Invalid {coord_name} coordinates: {error_msg}",
                }

        start_point = Point(start_lon, start_lat, srid=4326)
        end_point = Point(end_lon, end_lat, srid=4326)

        vertex_threshold = kwargs.get("vertex_threshold", 0.01)

        return ScenicRouteOrchestrator.find_best_scenic_route_with_constraint(
            start_point=start_point,
            end_point=end_point,
            preference=preference,
            vertex_threshold=vertex_threshold,
        )

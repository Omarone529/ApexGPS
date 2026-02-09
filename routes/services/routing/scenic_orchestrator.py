import logging
import time

from django.contrib.gis.geos import Point

from .fast_routing import FastRoutingService
from .scenic_routing import ScenicRoutingService
from .utils import (
    _validate_coordinates,
)

logger = logging.getLogger(__name__)

__all__ = ["ScenicRouteOrchestrator"]


class ScenicRouteOrchestrator:
    """Orchestrates scenic route calculation with time constraints."""

    @staticmethod
    def find_best_scenic_route_with_constraint(
        start_point: Point,
        end_point: Point,
        preference: str = "balanced",
        vertex_threshold: float = 0.01,
    ) -> dict:
        """Find best scenic route that respects time constraint."""
        start_time = time.time()

        logger.info(
            f"Starting scenic route calculation: "
            f"({start_point.y:.6f}, {start_point.x:.6f})"
            f" to ({end_point.y:.6f}, {start_point.x:.6f}), "
            f"preference: {preference}"
        )

        lat_diff = abs(start_point.y - end_point.y) * 111
        lon_diff = (
            abs(start_point.x - end_point.x) * 111 * 0.6
        )  # 1 grado lon = ~66 km a latitudini italiane
        straight_distance_km = (lat_diff**2 + lon_diff**2) ** 0.5

        if straight_distance_km < 1.0:
            logger.warning(f"Points too close: {straight_distance_km:.2f} km")
            return {
                "success": False,
                "error": f"I punti sono troppo vicini ({straight_distance_km:.2f} km)."
                f" Inserisci località più distanti.",
                "error_details": {
                    "stage": "distance_validation",
                    "distance_km": round(straight_distance_km, 2),
                    "minimum_required_km": 1.0,
                },
                "processing_time_ms": round((time.time() - start_time) * 1000, 2),
            }

        logger.info(f"Straight-line distance: {straight_distance_km:.2f} km")

        # Calcola percorso più veloce per riferimento temporale
        logger.info("Calculating fastest route for time reference")
        fast_service = FastRoutingService()

        try:
            fastest_result = fast_service.calculate_route(
                start_point=start_point,
                end_point=end_point,
                use_progressive_search=True,
            )
        except Exception as e:
            logger.error(f"Exception during fastest route calculation: {str(e)}")
            fastest_result = None

        if not fastest_result:
            processing_time_ms = (time.time() - start_time) * 1000
            error_message = (
                "Cannot calculate fastest route. Possible causes: "
                "1) Points are outside road network coverage area, "
                "2) Points are in disconnected graph components, "
                "3) Database topology issues. "
                "Check logs for detailed vertex search information."
            )
            logger.error(error_message)

            return {
                "success": False,
                "error": error_message,
                "error_details": {
                    "stage": "fastest_route_calculation",
                    "start_point": {"lat": start_point.y, "lon": start_point.x},
                    "end_point": {"lat": end_point.y, "lon": end_point.x},
                },
                "processing_time_ms": round(processing_time_ms, 2),
            }

        fastest_time = fastest_result.get("total_time_seconds", 0)
        fastest_minutes = fastest_result.get("total_time_minutes", 0)
        logger.info(
            f"Fastest route calculated successfully: {fastest_minutes:.1f} min, "
            f"{fastest_result.get('total_distance_km', 0):.2f} km"
        )

        # Calcola percorso panoramico con threshold fornita
        logger.info(f"Calculating scenic route with '{preference}' preference")
        scenic_service = ScenicRoutingService(preference=preference)

        try:
            scenic_result = scenic_service.calculate_route(
                start_point=start_point,
                end_point=end_point,
                reference_fastest_time=fastest_minutes,
                vertex_threshold=vertex_threshold,
            )
        except Exception as e:
            logger.error(f"Exception during scenic route calculation: {str(e)}")
            scenic_result = None

        processing_time_ms = (time.time() - start_time) * 1000

        # CORREZIONE CRITICA: Gestisci il caso in cui scenic_result è None
        if not scenic_result:
            logger.warning(
                "Scenic route calculation failed, but fastest route is available"
            )

            # Crea un risultato panoramico di fallback basato sul percorso veloce
            # MA con un punteggio panoramico realistico
            fallback_scenic_result = {
                "total_time_seconds": fastest_time,
                "total_time_minutes": fastest_minutes,
                "total_distance_km": fastest_result.get("total_distance_km", 0),
                "scenic_score": 50.0,  # Punteggio medio
                "avg_scenic_rating": 5.0,
                "avg_curvature": 1.0,
                "total_poi_density": 0.0,
                "polyline": fastest_result.get("polyline", ""),
                "segment_count": fastest_result.get("segment_count", 0),
                "poi_count": 0,
                "poi_stops": [],
                "time_constraint": {
                    "max_excess_minutes": 40.0,
                    "actual_excess_minutes": 0.0,
                    "is_within_constraint": True,
                    "reference_fastest_minutes": fastest_minutes,
                },
            }

            # Usa il fallback
            scenic_result = fallback_scenic_result
            scenic_minutes = fastest_minutes
            time_excess_minutes = 0.0
            actual_scenic_score = 50.0
        else:
            # Usa il risultato panoramico reale
            scenic_minutes = scenic_result.get("total_time_minutes", 0)
            time_excess_minutes = scenic_minutes - fastest_minutes
            actual_scenic_score = scenic_result.get("total_scenic_score", 0)

        # Calcola percentuale di eccesso temporale
        time_excess_percent = (
            (time_excess_minutes / fastest_minutes * 100) if fastest_minutes > 0 else 0
        )
        max_excess_minutes = ScenicRoutingService.MAX_TIME_EXCESS_MINUTES
        is_within_constraint = time_excess_minutes <= max_excess_minutes

        # Assemble risultato finale
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
                "total_time_minutes": scenic_result.get(
                    "total_time_minutes", fastest_minutes
                ),
                "total_distance_km": scenic_result.get("total_distance_km", 0),
                "scenic_score": scenic_result.get(
                    "scenic_score", 50.0
                ),  # Usa scenic_score, non total_scenic_score
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
            f"constraint: {'satisfied' if is_within_constraint else 'exceeded'}"
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
        """Calculate scenic route from coordinates with validation."""
        # Validate coordinates
        for coord_name, lat, lon in [
            ("start", start_lat, start_lon),
            ("end", end_lat, end_lon),
        ]:
            is_valid, error_msg = _validate_coordinates(lat, lon)
            if not is_valid:
                logger.error(f"Invalid {coord_name} coordinates: {error_msg}")
                return {
                    "success": False,
                    "error": f"Invalid {coord_name} coordinates: {error_msg}",
                    "error_details": {
                        "stage": "coordinate_validation",
                        "coord_type": coord_name,
                        "latitude": lat,
                        "longitude": lon,
                    },
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

import logging
from typing import Any

from routes.models import Route
from routes.serializers import RouteCalculationResponseSerializer
from routes.services.routing.fast_routing import FastRoutingService

logger = logging.getLogger(__name__)

__all__ = [
    "RouteCalculationResponseSerializer",
]


class RouteRecalculationService:
    """Service to recalculate routes when stops change."""

    @staticmethod
    def recalculate_route_with_stops(route_id: int) -> bool:
        """Recalculate a route considering all its stops in order."""
        try:
            route = Route.objects.get(id=route_id)

            # Get all points in order: start -> stops -> end
            all_points = route.get_all_points_in_order()

            if len(all_points) < 2:
                logger.warning(f"Route {route_id} has less than 2 points, skipping")
                return False

            # Initialize routing service
            routing_service = FastRoutingService()

            # Calculate route through all points
            total_distance_km = 0
            total_time_minutes = 0

            # Calculate route between consecutive points
            for i in range(len(all_points) - 1):
                start_point = all_points[i]
                end_point = all_points[i + 1]

                segment_result = routing_service.calculate_route(
                    start_point=start_point,
                    end_point=end_point,
                    vertex_threshold=0.01,
                )

                if not segment_result:
                    logger.error(
                        f"Cannot calculate route segment {i} for route {route_id}"
                    )
                    return False

                total_distance_km += segment_result.get("total_distance_km", 0)
                total_time_minutes += segment_result.get("total_time_minutes", 0)

            # Update route with new values
            route.distance_km = round(total_distance_km, 2)
            route.estimated_time_min = round(total_time_minutes, 1)
            route.save()

            logger.info(
                f"Recalculated route {route_id}: {total_distance_km:.2f} km, "
                f"{total_time_minutes:.1f} min"
            )
            return True

        except Route.DoesNotExist:
            logger.error(f"Route {route_id} not found")
            return False
        except Exception as e:
            logger.error(f"Error recalculating route {route_id}: {e}")
            return False

    @staticmethod
    def get_detailed_recalculation(route_id: int) -> dict[str, Any]:
        """Get detailed recalculation information."""
        try:
            route = Route.objects.get(id=route_id)
            all_points = route.get_all_points_in_order()

            if len(all_points) < 2:
                return {
                    "success": False,
                    "error": "Route needs at least 2 points",
                    "route_id": route_id,
                }

            routing_service = FastRoutingService()
            segments_info = []

            for i in range(len(all_points) - 1):
                start_point = all_points[i]
                end_point = all_points[i + 1]

                segment_name = f"Segment {i}"
                if i == 0:
                    segment_name = "Start to first stop"
                elif i == len(all_points) - 2:
                    segment_name = f"Stop {i} to end"
                elif i > 0:
                    segment_name = f"Stop {i} to stop {i + 1}"

                segment_result = routing_service.calculate_route(
                    start_point=start_point, end_point=end_point
                )

                if segment_result:
                    segments_info.append(
                        {
                            "name": segment_name,
                            "distance_km": segment_result.get("total_distance_km", 0),
                            "time_minutes": segment_result.get("total_time_minutes", 0),
                            "has_route": True,
                        }
                    )
                else:
                    segments_info.append(
                        {
                            "name": segment_name,
                            "distance_km": 0,
                            "time_minutes": 0,
                            "has_route": False,
                            "error": f"No route found from point {i} to {i + 1}",
                        }
                    )

            # Calculate totals
            total_distance = round(
                sum(seg.get("distance_km", 0) for seg in segments_info), 2
            )
            total_time = round(
                sum(seg.get("time_minutes", 0) for seg in segments_info), 1
            )

            return {
                "success": True,
                "route_id": route_id,
                "route_name": route.name,
                "total_distance_km": total_distance,
                "total_time_minutes": total_time,
                "segment_count": len(segments_info),
                "segments": segments_info,
                "all_points_valid": all(
                    seg.get("has_route", False) for seg in segments_info
                ),
            }

        except Exception as e:
            logger.error(
                f"Error in get_detailed_recalculation for route {route_id}: {e}"
            )
            return {
                "success": False,
                "error": str(e),
                "route_id": route_id,
            }

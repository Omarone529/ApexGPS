import logging
from typing import Any

from routes.models import Route
from routes.services.routing.fast_routing import FastRoutingService
from routes.services.routing.utils import _calculate_route_segments

logger = logging.getLogger(__name__)

__all__ = [
    "RouteRecalculationService",
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

            # Calculate route through all points using utility function
            routing_service = FastRoutingService()
            route_result = _calculate_route_segments(routing_service, all_points)

            if not route_result.get("success") or not route_result.get(
                "all_segments_valid", False
            ):
                logger.error(f"Cannot calculate complete route for route {route_id}")
                return False

            # Update route with new values
            route.distance_km = round(route_result["total_distance_km"], 2)
            route.estimated_time_min = round(route_result["total_time_minutes"], 1)
            route.save()

            logger.info(
                f"Recalculated route {route_id}: {route.distance_km:.2f} km, "
                f"{route.estimated_time_min:.1f} min"
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
        """Get detailed recalculation information for debugging."""
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
            route_result = _calculate_route_segments(routing_service, all_points)

            if not route_result.get("success"):
                return {
                    "success": False,
                    "error": "Route calculation failed",
                    "route_id": route_id,
                    "details": route_result,
                }

            # Format segment names
            segments_info = []
            for i, segment in enumerate(route_result.get("segments", [])):
                segment_name = f"Segment {i}"
                if i == 0:
                    segment_name = "Start to first stop"
                elif i == len(all_points) - 2:
                    segment_name = f"Stop {i - 1} to end"
                elif i > 0:
                    segment_name = f"Stop {i - 1} to stop {i}"

                segments_info.append(
                    {
                        "name": segment_name,
                        "distance_km": segment.get("distance_km", 0),
                        "time_minutes": segment.get("time_minutes", 0),
                        "has_route": segment.get("success", False),
                    }
                )

            return {
                "success": True,
                "route_id": route_id,
                "route_name": route.name,
                "total_distance_km": round(route_result["total_distance_km"], 2),
                "total_time_minutes": round(route_result["total_time_minutes"], 1),
                "segment_count": route_result["segment_count"],
                "segments": segments_info,
                "all_points_valid": route_result["all_segments_valid"],
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

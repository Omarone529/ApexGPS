from abc import ABC, abstractmethod

__all__ = ["BaseRoutingService"]

from django.contrib.gis.geos import Point


class BaseRoutingService(ABC):
    """Abstract base class for all routing services."""

    # Default distance threshold for vertex snapping (approx 1km)
    DEFAULT_VERTEX_THRESHOLD = 0.01  # degrees

    @abstractmethod
    def calculate_route(
        self, start_point: Point, end_point: Point, **kwargs
    ) -> dict | None:
        """Abstract method to calculate route between two points."""

    @abstractmethod
    def get_cost_column(self) -> str:
        """Get the cost column to use for this routing algorithm."""

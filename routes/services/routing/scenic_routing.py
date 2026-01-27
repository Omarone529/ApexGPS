import logging

from django.contrib.gis.geos import Point
from django.db import connection

from .base_routing import BaseRoutingService
from .utils import (
    _calculate_path_metrics,
    _create_route_geometry,
    _encode_linestring_to_polyline,
    _execute_dijkstra_query,
    _extract_edges_from_dijkstra_result,
    _find_nearest_vertex,
    _get_secondary_road_percentage,
    _get_segments_by_ids,
    _validate_coordinates,
)

logger = logging.getLogger(__name__)

__all__ = ["POIStop", "ScenicRoutingService"]


class POIStop:
    """Represents a Point of Interest stop along a scenic motorcycle route."""

    def __init__(
        self,
        poi_id: int,
        name: str,
        category: str,
        location: Point,
        scenic_value: float,
    ):
        """Initialize POIStop object."""
        self.poi_id = poi_id
        self.name = name
        self.category = category
        self.location = location
        self.scenic_value = scenic_value

    def to_dict(self) -> dict:
        """Convert POI stop to dictionary for API response."""
        return {
            "poi_id": self.poi_id,
            "name": self.name,
            "category": self.category,
            "location": {"lat": self.location.y, "lon": self.location.x},
            "scenic_value": round(self.scenic_value, 2),
        }


class ScenicRoutingService(BaseRoutingService):
    """
    Calculates scenic motorcycle routes that balance travel time with scenic quality.

    Key features:
    - Includes Points of Interest as mandatory stops
    - Uses pre-calculated scenic metrics from RoadSegment
    - Respects 40-minute time constraint vs fastest route
    - Considers POI density, scenic rating, and road curvature
    - Finds nearest road network vertices for start/end points
    - Calculates initial scenic route to identify potential POIs
    - Selects highest-value POIs within route proximity
    - Recalculates route through selected POIs
    - Validates against 40-minute time constraint
    """

    # Maximum time increase vs fastest route (40 minutes absolute)
    MAX_TIME_EXCESS_MINUTES = 40.0

    # Added limits to avoid excessive deviations
    MAX_POI_DISTANCE_M = 2500.0
    MIN_POI_SCENIC_VALUE = 2.0
    MAX_DETOUR_FACTOR = 1.4

    # Configuration for different scenic preference levels
    PREFERENCE_CONFIGS = {
        "fast": {
            "time_weight": 0.70,
            "poi_weight": 0.20,
            "scenic_weight": 0.08,
            "curvature_weight": 0.02,
            "min_pois": 1,
            "max_pois": 6,
            "max_poi_distance_m": 1500.0,
            "description": "Fast scenic route with minimal POI stops",
        },
        "balanced": {
            "time_weight": 0.50,
            "poi_weight": 0.30,
            "scenic_weight": 0.15,
            "curvature_weight": 0.05,
            "min_pois": 1,  # Ridotto da 2 a 1 (può essere 0)
            "max_pois": 4,  # Ridotto da 5 a 4
            "max_poi_distance_m": 2000.0,  # Aggiunto
            "description": "Balanced mix of speed, scenery, and POI stops",
        },
        "most_winding": {
            "time_weight": 0.30,
            "poi_weight": 0.20,
            "scenic_weight": 0.25,
            "curvature_weight": 0.25,
            "min_pois": 3,
            "max_pois": 8,
            "max_poi_distance_m": 2500.0,
            "description": "Emphasizes winding roads and maximum POI stops",
        },
    }

    def __init__(self, preference: str = "balanced"):
        """Initialize scenic routing service with specified preference."""
        if preference not in self.PREFERENCE_CONFIGS:
            raise ValueError(
                f"Preference must be one of: {list(self.PREFERENCE_CONFIGS.keys())}"
            )

        self.preference = preference
        self.config = self.PREFERENCE_CONFIGS[preference]
        logger.debug(f"Initialized ScenicRoutingService with preference: {preference}")

    def get_cost_column(self) -> str:
        """
        Generate SQL expression for scenic routing cost calculation.

        The cost function combines four factors with configurable weights:
        - Travel time (normalized to minutes)
        - POI density (weighted, inverted: higher density = lower cost)
        - Scenic rating (0-10 scale, inverted: higher rating = lower cost)
        - Road curvature (≥1.0, inverted: higher curvature = lower cost)
        """
        time_weight = self.config["time_weight"]
        poi_weight = self.config["poi_weight"]
        scenic_weight = self.config["scenic_weight"]
        curvature_weight = self.config["curvature_weight"]

        # Normalize travel time (seconds to minutes)
        time_component = "cost_time / 60.0"

        # Normalize weighted POI density (0-100 scale, inverted)
        poi_component = (
            "(100.0 - LEAST(COALESCE(weighted_poi_density, 0) * 10, 100)) / 100.0"
        )

        # Normalize scenic rating (0-10 scale, inverted)
        scenic_component = "(10.0 - COALESCE(scenic_rating, 5.0)) / 10.0"

        # Normalize curvature (≥1.0, inverted)
        curvature_component = "(2.0 - LEAST(COALESCE(curvature, 1.0), 2.0))"

        # Combine weighted components
        cost_expression = (
            f"({time_component} * {time_weight}) + "
            f"({poi_component} * {poi_weight}) + "
            f"({scenic_component} * {scenic_weight}) + "
            f"({curvature_component} * {curvature_weight})"
        )

        logger.debug(f"Generated cost expression for {self.preference} preference")
        return cost_expression

    def _find_pois_along_route(
        self, segments: list[dict], max_distance_m: float = 500.0
    ) -> list[POIStop]:
        """Find Points of Interest within specified distance of route segments."""
        if not segments:
            logger.debug("No segments provided for POI search")
            return []

        # Extract segment IDs for database query
        segment_ids = [seg["id"] for seg in segments]
        try:
            with connection.cursor() as cursor:
                # Modificata query per includere distanza e filtrare POI troppo lontani
                query = """
                SELECT
                    poi.id,
                    poi.name,
                    poi.category,
                    ST_AsText(poi.location) as location_wkt,
                    poi.importance_score,
                    COUNT(*) as nearby_segment_count,
                    MIN(ST_Distance(poi.location, seg.geometry)) as min_distance
                FROM gis_data_pointofinterest poi
                INNER JOIN gis_data_roadsegment seg
                    ON ST_DWithin(poi.location, seg.geometry, %s)
                WHERE seg.id = ANY(%s::int[])
                    AND poi.is_active = true
                GROUP BY poi.id, poi.name, poi.category,
                 poi.location, poi.importance_score
                HAVING MIN(ST_Distance(poi.location, seg.geometry)) <= %s
                    AND poi.importance_score >= %s
                ORDER BY poi.importance_score DESC, min_distance ASC
                LIMIT %s
                """

                cursor.execute(
                    query,
                    [
                        max_distance_m * 2,
                        segment_ids,
                        self.config.get("max_poi_distance_m", self.MAX_POI_DISTANCE_M),
                        self.MIN_POI_SCENIC_VALUE,
                        self.config["max_pois"] * 3,  # Get more for filtering
                    ],
                )

                pois = []
                for row in cursor.fetchall():
                    (
                        poi_id,
                        name,
                        category,
                        location_wkt,
                        importance_score,
                        segment_count,
                        min_distance,
                    ) = row

                    # Parse WKT point format to Point object
                    if location_wkt and location_wkt.startswith("POINT"):
                        coords = location_wkt.replace("POINT(", "").replace(")", "")
                        lon, lat = map(float, coords.split())
                        location = Point(lon, lat, srid=4326)

                        # Calculate scenic value with distance penalty
                        scenic_value = self._calculate_poi_scenic_value(
                            category, importance_score, segment_count, min_distance
                        )

                        pois.append(
                            POIStop(
                                poi_id=poi_id,
                                name=name,
                                category=category,
                                location=location,
                                scenic_value=scenic_value,
                            )
                        )

                # Sort by scenic value and limit to maximum allowed
                pois.sort(key=lambda p: p.scenic_value, reverse=True)
                selected_pois = pois[: self.config["max_pois"]]

                logger.info(
                    f"Found {len(selected_pois)} valid POIs near route "
                    f"(from {len(pois)} candidates)"
                )
                return selected_pois

        except Exception as e:
            logger.error(f"Error finding POIs along route: {str(e)}", exc_info=True)
            return []

    def _calculate_poi_scenic_value(
        self,
        category: str,
        importance_score: float,
        segment_count: int,
        distance_m: float = 0.0,
    ) -> float:
        """Calculate scenic value score for a Point of Interest."""
        category_weights = {
            "panoramic": 3.0,
            "mountain_pass": 3.5,
            "twisty_road": 4.0,
            "viewpoint": 3.0,
            "lake": 2.5,
            "waterfall": 2.8,
            "castle": 2.0,
            "vineyard": 1.8,
            "default": 1.0,
        }

        base_weight = category_weights.get(category, category_weights["default"])

        # Proximity factor: POIs closer to more route segments are better
        # Max 2x bonus for POIs very close to route
        proximity_factor = min(segment_count / 3.0, 2.0)

        max_allowed_distance = self.config.get(
            "max_poi_distance_m", self.MAX_POI_DISTANCE_M
        )
        distance_penalty = 1.0 - min(distance_m / max_allowed_distance, 0.5)

        scenic_value = (
            base_weight * importance_score * proximity_factor * distance_penalty
        )
        return round(scenic_value, 2)

    def _calculate_route_scenic_metrics(self, segments: list[dict]) -> dict[str, float]:
        """
        Calculate comprehensive scenic metrics for a complete route.
        Calculate overall scenic score (0-100 scale)
        35% from scenic rating
        35% from POI density
        20% from curvature
        10% from secondary roads.
        """
        if not segments:
            logger.debug("No segments provided for scenic metrics calculation")
            return {
                "total_scenic_score": 0.0,
                "avg_scenic_rating": 0.0,
                "total_poi_density": 0.0,
                "avg_curvature": 1.0,
                "scenic_segment_count": 0,
                "scenic_percentage": 0.0,
                "total_segments": 0,
                "total_length_km": 0.0,
            }

        total_length_m = 0.0
        total_scenic = 0.0
        total_poi_density = 0.0
        total_curvature = 0.0
        scenic_segment_count = 0

        # Calculate weighted sums (weighted by segment length)
        for segment in segments:
            length_m = segment.get("length_m", 0)
            scenic_rating = segment.get("scenic_rating", 5.0)  # Default 5/10
            poi_density = segment.get("poi_density", 0.0)
            curvature = segment.get("curvature", 1.0)  # Default 1.0 (straight)

            total_length_m += length_m
            total_scenic += scenic_rating * length_m
            total_poi_density += poi_density * length_m
            total_curvature += curvature * length_m

            # Count segments with high scenic rating (≥6/10)
            if scenic_rating >= 6.0:
                scenic_segment_count += 1

        # Calculate weighted averages
        total_length_km = total_length_m / 1000
        avg_scenic = total_scenic / total_length_m if total_length_m > 0 else 0.0
        avg_poi_density = (
            total_poi_density / total_length_m if total_length_m > 0 else 0.0
        )
        avg_curvature = total_curvature / total_length_m if total_length_m > 0 else 1.0

        # Calculate secondary road percentage using utility function
        secondary_road_percent = _get_secondary_road_percentage(segments)
        scenic_score = (
            (avg_scenic / 10.0 * 35)
            + (min(avg_poi_density * 10, 35))  # scenic_rating is 0-10
            + ((avg_curvature - 1.0) * 100 * 0.2)  # poi_density with scaling
            + (  # curvature bonus (1.0 = straight)
                secondary_road_percent * 0.1
            )  # bonus for secondary roads
        )
        scenic_score = min(100.0, max(0.0, scenic_score))

        # Calculate percentage of scenic segments
        scenic_percentage = (
            (scenic_segment_count / len(segments) * 100) if segments else 0.0
        )

        metrics = {
            "total_scenic_score": round(scenic_score, 1),
            "avg_scenic_rating": round(avg_scenic, 2),
            "total_poi_density": round(avg_poi_density, 2),
            "avg_curvature": round(avg_curvature, 3),
            "total_length_km": round(total_length_km, 2),
            "scenic_segment_count": scenic_segment_count,
            "scenic_percentage": round(scenic_percentage, 1),
            "total_segments": len(segments),
            "secondary_road_percent": round(secondary_road_percent, 1),
        }

        logger.debug(f"Calculated scenic metrics: {metrics['total_scenic_score']}/100")
        return metrics

    def _calculate_scenic_route_basic(
        self, start_vertex: int, end_vertex: int
    ) -> list[int] | None:
        """Calculate basic scenic route between two vertices using Dijkstra."""
        cost_column = self.get_cost_column()

        try:
            dijkstra_result = _execute_dijkstra_query(
                start_vertex, end_vertex, cost_column
            )

            if not dijkstra_result:
                logger.warning(f"No Dijkstra result for {start_vertex}->{end_vertex}")
                return None

            edge_ids = _extract_edges_from_dijkstra_result(dijkstra_result)

            if edge_ids:
                logger.debug(f"Found basic scenic route with {len(edge_ids)} edges")
                return edge_ids
            else:
                logger.warning(
                    f"No edges in Dijkstra result for {start_vertex}->{end_vertex}"
                )
                return None

        except Exception as e:
            logger.error(
                f"Error in basic scenic route calculation: {str(e)}", exc_info=True
            )
            return None

    def calculate_route(
        self, start_point: Point, end_point: Point, **kwargs
    ) -> dict | None:
        """
        Calculate scenic motorcycle route between two geographic points.

        The algorithm:
        - Snap start/end points to nearest road network vertices
        - Calculate initial scenic route without POI constraints
        - Identify high-value POIs near the route
        - Recalculate route through selected POIs
        - Validate against 40-minute time constraint
        - Return complete route with metrics and POI information
        """
        vertex_threshold = kwargs.get("vertex_threshold", self.DEFAULT_VERTEX_THRESHOLD)
        reference_fastest_time = kwargs.get("reference_fastest_time")
        max_time_excess_minutes = kwargs.get(
            "max_time_excess_minutes", self.MAX_TIME_EXCESS_MINUTES
        )

        logger.info(
            f"Starting scenic route calculation ({self.preference}) "
            f"from {start_point} to {end_point}"
        )

        # Find nearest road network vertices
        start_vertex = _find_nearest_vertex(start_point, vertex_threshold)
        end_vertex = _find_nearest_vertex(end_point, vertex_threshold)

        if not start_vertex or not end_vertex:
            logger.warning(
                f"Cannot find road vertices: start={start_vertex}, end={end_vertex}"
            )
            return None
        logger.debug(f"Found vertices: start={start_vertex}, end={end_vertex}")

        try:
            # Calculate initial route to identify potential POI areas
            basic_edges = self._calculate_scenic_route_basic(start_vertex, end_vertex)
            if not basic_edges:
                logger.warning("No basic scenic route found")
                return None

            basic_segments = _get_segments_by_ids(basic_edges)
            if not basic_segments:
                logger.warning("Cannot retrieve basic route segments")
                return None
            logger.debug(f"Basic route has {len(basic_segments)} segments")

            # Calculate basic route metrics for comparison
            basic_metrics = _calculate_path_metrics(basic_segments)
            basic_time = basic_metrics.get("total_time_minutes", 0)

            # Find POIs along the basic route
            pois = self._find_pois_along_route(basic_segments)
            logger.info(f"Identified {len(pois)} potential POIs")

            if pois:
                # Try to include POIs in route with time constraint check
                route_edges, included_pois = self._build_route_through_pois(
                    start_vertex,
                    end_vertex,
                    pois,
                    reference_fastest_time,
                    max_time_excess_minutes,
                    basic_time,
                )
            else:
                # No POIs found, use basic route
                route_edges = basic_edges
                included_pois = []
                logger.info("No valid POIs found, using basic scenic route")

            # Get final route segments and calculate metrics
            final_segments = _get_segments_by_ids(route_edges)
            if not final_segments:
                logger.warning("Cannot retrieve final route segments")
                return None

            route_metrics = _calculate_path_metrics(final_segments)
            scenic_metrics = self._calculate_route_scenic_metrics(final_segments)

            # Create route geometry
            route_geometry = _create_route_geometry(final_segments)
            polyline_encoded = _encode_linestring_to_polyline(route_geometry)

            # Validate time constraint
            time_excess_minutes = 0.0
            is_within_constraint = True

            if reference_fastest_time and route_metrics["total_time_minutes"] > 0:
                time_excess_minutes = (
                    route_metrics["total_time_minutes"] - reference_fastest_time
                )
                is_within_constraint = time_excess_minutes <= max_time_excess_minutes

                logger.info(
                    f"Time constraint: {time_excess_minutes:.1f}min excess "
                    f"(limit: {max_time_excess_minutes}min) - "
                    f"{'OK' if is_within_constraint else 'EXCEEDED'}"
                )

            # Step 8: Assemble final result
            result = {
                "route_type": "scenic",
                "preference": self.preference,
                "preference_description": self.config["description"],
                "start_vertex": start_vertex,
                "end_vertex": end_vertex,
                # Route metrics
                **route_metrics,
                **scenic_metrics,
                # Geometry
                "polyline": polyline_encoded,
                "geometry": route_geometry,
                "segments": final_segments[:10],  # First 10 segments for debugging
                "total_segments": len(final_segments),
                # POI information
                "poi_stops": [poi.to_dict() for poi in included_pois],
                "poi_count": len(included_pois),
                # Time constraint validation
                "time_constraint": {
                    "max_excess_minutes": max_time_excess_minutes,
                    "actual_excess_minutes": round(time_excess_minutes, 1),
                    "is_within_constraint": is_within_constraint,
                    "reference_fastest_minutes": reference_fastest_time,
                },
                # Configuration details
                "cost_weights": {
                    "time": self.config["time_weight"],
                    "poi": self.config["poi_weight"],
                    "scenic": self.config["scenic_weight"],
                    "curvature": self.config["curvature_weight"],
                },
                "poi_requirements": {
                    "min_pois": self.config["min_pois"],
                    "max_pois": self.config["max_pois"],
                    "actual_pois": len(included_pois),
                },
            }

            logger.info(
                f"Scenic route complete: "
                f"{scenic_metrics['total_scenic_score']}/100 scenic, "
                f"{len(included_pois)} POIs, "
                f"{route_metrics['total_distance_km']:.1f}km, "
                f"{route_metrics['total_time_minutes']:.0f}min"
            )

            return result

        except Exception as e:
            logger.error(f"Error calculating scenic route: {str(e)}", exc_info=True)
            return None

    def _build_route_through_pois(
        self,
        start_vertex: int,
        end_vertex: int,
        pois: list[POIStop],
        reference_fastest_time: float | None,
        max_time_excess_minutes: float,
        basic_route_time: float = 0.0,
    ) -> tuple[list[int], list[POIStop]]:
        """Build route that includes specified POIs while respecting time constraint."""
        # Sort POIs by scenic value and try different combinations
        sorted_pois = sorted(pois, key=lambda p: p.scenic_value, reverse=True)

        min_pois = self.config["min_pois"]
        max_pois = min(self.config["max_pois"], len(sorted_pois))

        logger.debug(
            f"Trying to include {min_pois}-{max_pois} POIs from {len(pois)} candidates"
        )

        # Calculate basic route without POIs for comparison
        basic_edges = self._calculate_scenic_route_basic(start_vertex, end_vertex)
        if not basic_edges:
            return [], []

        best_route_edges = basic_edges
        best_pois = []
        best_score = 0.0

        # Try different numbers of POIs (from min to max)
        for poi_count in range(min_pois, max_pois + 1):
            selected_pois = sorted_pois[:poi_count]
            logger.debug(
                f"Trying route with {poi_count} POIs: {[p.name for p in selected_pois]}"
            )

            try:
                # Build route through selected POIs in scenic value order
                route_edges = []
                included_pois = []
                current_vertex = start_vertex

                for poi in selected_pois:
                    # Find nearest vertex to POI location
                    poi_vertex = _find_nearest_vertex(
                        poi.location, distance_threshold=0.01
                    )

                    if not poi_vertex:
                        logger.debug(f"Cannot find vertex near POI: {poi.name}")
                        continue

                    # Calculate route segment to this POI
                    segment_edges = self._calculate_scenic_route_basic(
                        current_vertex, poi_vertex
                    )
                    if not segment_edges:
                        logger.debug(f"No route to POI: {poi.name}")
                        break

                    route_edges.extend(segment_edges)
                    included_pois.append(poi)
                    current_vertex = poi_vertex

                if not included_pois:
                    continue

                # Add final segment from last POI to destination
                final_segment = self._calculate_scenic_route_basic(
                    current_vertex, end_vertex
                )
                if not final_segment:
                    continue

                route_edges.extend(final_segment)

                segments = _get_segments_by_ids(route_edges)
                if not segments:
                    continue

                metrics = _calculate_path_metrics(segments)
                route_time = metrics["total_time_minutes"]

                # Calculate detour factor
                detour_factor = (
                    route_time / basic_route_time if basic_route_time > 0 else 1.0
                )

                # Check time constraint if reference available
                time_ok = True
                if reference_fastest_time:
                    time_excess = route_time - reference_fastest_time
                    time_ok = time_excess <= max_time_excess_minutes

                # Check detour constraint
                detour_ok = detour_factor <= self.MAX_DETOUR_FACTOR

                # Calculate route scenic score
                scenic_metrics = self._calculate_route_scenic_metrics(segments)
                route_score = scenic_metrics["total_scenic_score"]

                # Update best route if this one is better
                if time_ok and detour_ok and route_score > best_score:
                    best_score = route_score
                    best_route_edges = route_edges
                    best_pois = included_pois
                    logger.debug(f"New best route found with score {route_score}")

            except Exception as e:
                logger.debug(f"Error building route with {poi_count} POIs: {str(e)}")
                continue

        # Return the best route found
        if best_pois:
            logger.info(
                f"Selected optimal route with {len(best_pois)} POIs,"
                f" scenic score: {best_score}"
            )
            return best_route_edges, best_pois
        else:
            logger.warning(
                "No valid POI routes found within constraints, using basic scenic route"
            )
            return basic_edges, []

    def calculate_scenic_route(
        self,
        start_lat: float,
        start_lon: float,
        end_lat: float,
        end_lon: float,
        reference_fastest_time: float | None = None,
        **kwargs,
    ) -> dict | None:
        """Calculate scenic route from geographic coordinates."""
        # Validate input coordinates
        for coord_name, lat, lon in [
            ("start", start_lat, start_lon),
            ("end", end_lat, end_lon),
        ]:
            is_valid, error_msg = _validate_coordinates(lat, lon)
            if not is_valid:
                raise ValueError(f"Invalid {coord_name} coordinates: {error_msg}")

        # Convert to Point objects
        start_point = Point(start_lon, start_lat, srid=4326)
        end_point = Point(end_lon, end_lat, srid=4326)

        # Delegate to main calculation method
        return self.calculate_route(
            start_point=start_point,
            end_point=end_point,
            reference_fastest_time=reference_fastest_time,
            **kwargs,
        )

    def calculate_with_fastest_reference(
        self,
        start_lat: float,
        start_lon: float,
        end_lat: float,
        end_lon: float,
        **kwargs,
    ) -> dict:
        """
        Calculate scenic route with automatic fastest route comparison.

        This method:
        - Calculates fastest route for baseline
        - Calculates scenic route with time constraint
        - Returns comparison of both routes
        """
        from .fast_routing import FastRoutingService

        logger.info("Calculating scenic route with fastest reference")

        # Step 1: Calculate fastest route for baseline
        fast_service = FastRoutingService()
        fastest_route = fast_service.calculate_fastest_route(
            start_lat=start_lat,
            start_lon=start_lon,
            end_lat=end_lat,
            end_lon=end_lon,
            **kwargs,
        )

        if not fastest_route:
            return {
                "success": False,
                "error": "Cannot calculate fastest route for comparison",
            }

        fastest_minutes = fastest_route.get("total_time_minutes", 0)
        logger.info(f"Fastest route: {fastest_minutes:.1f} minutes")

        # Step 2: Calculate scenic route with time constraint
        scenic_route = self.calculate_scenic_route(
            start_lat=start_lat,
            start_lon=start_lon,
            end_lat=end_lat,
            end_lon=end_lon,
            reference_fastest_time=fastest_minutes,
            **kwargs,
        )

        if not scenic_route:
            return {
                "success": False,
                "error": "Cannot calculate scenic route",
                "fastest_route": {
                    "total_time_minutes": fastest_minutes,
                    "total_distance_km": fastest_route.get("total_distance_km", 0),
                    "polyline": fastest_route.get("polyline", ""),
                },
            }

        # Step 3: Calculate comparison metrics
        scenic_minutes = scenic_route.get("total_time_minutes", 0)
        time_excess = scenic_minutes - fastest_minutes
        time_excess_percent = (
            (time_excess / fastest_minutes * 100) if fastest_minutes > 0 else 0
        )

        comparison = {
            "success": True,
            "fastest_route": {
                "total_time_minutes": fastest_minutes,
                "total_distance_km": fastest_route.get("total_distance_km", 0),
                "polyline": fastest_route.get("polyline", ""),
                "segment_count": fastest_route.get("segment_count", 0),
            },
            "scenic_route": scenic_route,
            "comparison": {
                "time_excess_minutes": round(time_excess, 1),
                "time_excess_percent": round(time_excess_percent, 1),
                "is_within_constraint": time_excess <= self.MAX_TIME_EXCESS_MINUTES,
                "constraint_limit_minutes": self.MAX_TIME_EXCESS_MINUTES,
                "poi_count": scenic_route.get("poi_count", 0),
                "scenic_score": scenic_route.get("total_scenic_score", 0),
                "recommendation": "scenic"
                if time_excess <= self.MAX_TIME_EXCESS_MINUTES
                and scenic_route.get("total_scenic_score", 0) > 60
                else "fastest",
            },
        }

        logger.info(
            f"Comparison complete: "
            f"Scenic route +{time_excess:.1f}min ({time_excess_percent:.1f}%), "
            f"{scenic_route.get('poi_count', 0)} POIs, "
            f"{scenic_route.get('total_scenic_score', 0)}/100 scenic"
        )

        return comparison

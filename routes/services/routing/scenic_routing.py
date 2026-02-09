import logging
import time

from django.contrib.gis.geos import Point
from django.db import connection

from .base_routing import BaseRoutingService
from .fast_routing import FastRoutingService
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
        """Initialize POI stop."""
        self.poi_id = poi_id
        self.name = name
        self.category = category
        self.location = location
        self.scenic_value = scenic_value
        """Initialize class."""

    def to_dict(self) -> dict:
        """Convert class to dict."""
        return {
            "poi_id": self.poi_id,
            "name": self.name,
            "category": self.category,
            "location": {"lat": self.location.y, "lon": self.location.x},
            "scenic_value": round(self.scenic_value, 2),
        }


class ScenicRoutingService(BaseRoutingService):
    """Select best scenic route to show."""

    MAX_TIME_EXCESS_MINUTES = 40.0

    MAX_POI_DISTANCE_M = 800.0
    MIN_POI_SCENIC_VALUE = 3.0
    MAX_DETOUR_FACTOR = 1.2
    MAX_CIRCUITOUS_FACTOR = 2

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
            "time_weight": 0.45,
            "poi_weight": 0.15,
            "scenic_weight": 0.30,
            "curvature_weight": 0.05,
            "min_pois": 0,
            "max_pois": 2,
            "max_poi_distance_m": 800.0,
            "description": "Panoramico, pulito, intelligente. Evita autostrade.",
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
        """Initialize the routing service."""
        if preference not in self.PREFERENCE_CONFIGS:
            raise ValueError(
                f"Preference must be one of: {list(self.PREFERENCE_CONFIGS.keys())}"
            )

        self.preference = preference
        self.config = self.PREFERENCE_CONFIGS[preference]
        logger.debug(f"Initialized ScenicRoutingService with preference: {preference}")

        self._route_cache = {}
        self._poi_cache = {}

    def get_cost_column(self) -> str:
        """Get cost method."""
        time_weight = self.config["time_weight"]
        poi_weight = self.config["poi_weight"]
        scenic_weight = self.config["scenic_weight"]
        curvature_weight = self.config["curvature_weight"]

        time_component = "cost_time / 60.0"

        poi_component = (
            "(100.0 - LEAST(COALESCE(weighted_poi_density, 0) * 10, 100)) / 100.0"
        )

        scenic_component = "(10.0 - COALESCE(scenic_rating, 5.0)) / 10.0"

        curvature_component = "(2.0 - LEAST(COALESCE(curvature, 1.0), 2.0))"

        highway_penalty = """
            CASE
                WHEN highway IN ('motorway', 'motorway_link', 'trunk', 'trunk_link')
                 THEN 3.0
                WHEN highway IN ('primary', 'primary_link') THEN 1.8
                WHEN highway IN ('secondary', 'tertiary') THEN 0.9
                WHEN highway IN ('unclassified', 'residential', 'track', 'path')
                 THEN 0.8
                ELSE 1.0
            END
        """

        cost_expression = f"""
            (({time_component} * {time_weight}) +
             ({poi_component} * {poi_weight}) +
             ({scenic_component} * {scenic_weight}) +
             ({curvature_component} * {curvature_weight})) * {highway_penalty}
        """

        simplified = " ".join(cost_expression.split())
        logger.debug(f"Generated cost expression for {self.preference} preference")
        return simplified

    def get_secondary_cost_column(self) -> str:
        """Get secondary cost method if first fail."""
        time_weight = 0.4
        poi_weight = 0.25
        scenic_weight = 0.20
        curvature_weight = 0.15

        time_component = "cost_time / 60.0"
        poi_component = (
            "(100.0 - LEAST(COALESCE(weighted_poi_density, 0) * 10, 100)) / 100.0"
        )
        scenic_component = "(10.0 - COALESCE(scenic_rating, 5.0)) / 10.0"
        curvature_component = "(2.0 - LEAST(COALESCE(curvature, 1.0), 2.0))"

        secondary_bonus = """
            CASE
                WHEN highway = 'secondary' THEN 0.7
                WHEN highway = 'tertiary' THEN 0.7
                WHEN highway = 'unclassified' THEN 0.7
                WHEN highway = 'residential' THEN 0.7
                WHEN highway = 'track' THEN 0.7
                WHEN highway = 'path' THEN 0.7
                ELSE 1.3
            END
        """
        secondary_bonus = " ".join(secondary_bonus.split())

        cost_expression = (
            f"(({time_component} * {time_weight}) + "
            f"({poi_component} * {poi_weight}) + "
            f"({scenic_component} * {scenic_weight}) + "
            f"({curvature_component} * {curvature_weight})) * {secondary_bonus}"
        )

        logger.debug("Generated secondary road cost expression")
        return cost_expression

    def _find_pois_along_route(
        self, segments: list[dict], max_distance_m: float = 500.0
    ) -> list[POIStop]:
        """Search POI along route."""
        if not segments:
            logger.debug("No segments provided for POI search")
            return []

        segment_ids = [seg["id"] for seg in segments]
        cache_key = tuple(sorted(segment_ids))

        if cache_key in self._poi_cache:
            logger.debug(f"Returning POIs from cache for {len(segment_ids)} segments")
            return self._poi_cache[cache_key]

        try:
            with connection.cursor() as cursor:
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
                        self.config.get(
                            "min_poi_scenic_value", self.MIN_POI_SCENIC_VALUE
                        ),
                        self.config["max_pois"] * 3,
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

                    if location_wkt and location_wkt.startswith("POINT"):
                        coords = location_wkt.replace("POINT(", "").replace(")", "")
                        lon, lat = map(float, coords.split())
                        location = Point(lon, lat, srid=4326)

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

                pois.sort(key=lambda p: p.scenic_value, reverse=True)
                selected_pois = pois[: self.config["max_pois"]]

                logger.info(
                    f"Found {len(selected_pois)} valid POIs near route "
                    f"(from {len(pois)} candidates)"
                )

                self._poi_cache[cache_key] = selected_pois
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
        """Calculate scenic value based on importance score."""
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
        """Calculate scenic metrics based on segments."""
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

        for segment in segments:
            length_m = segment.get("length_m", 0)
            scenic_rating = segment.get("scenic_rating", 5.0)
            poi_density = segment.get("poi_density", 0.0)
            curvature = segment.get("curvature", 1.0)

            total_length_m += length_m
            total_scenic += scenic_rating * length_m
            total_poi_density += poi_density * length_m
            total_curvature += curvature * length_m

            if scenic_rating >= 6.0:
                scenic_segment_count += 1

        total_length_km = total_length_m / 1000
        avg_scenic = total_scenic / total_length_m if total_length_m > 0 else 0.0
        avg_poi_density = (
            total_poi_density / total_length_m if total_length_m > 0 else 0.0
        )
        avg_curvature = total_curvature / total_length_m if total_length_m > 0 else 1.0

        secondary_road_percent = _get_secondary_road_percentage(segments)

        scenic_component = (avg_scenic / 10.0) * 35

        poi_component = min(avg_poi_density * 3.5, 35)

        curvature_component = min((avg_curvature - 1.0) * 20, 20)

        secondary_component = min(secondary_road_percent * 0.1, 10)

        scenic_score = (
            scenic_component + poi_component + curvature_component + secondary_component
        )

        scenic_score = min(100.0, max(0.0, scenic_score))

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
            "score_components": {
                "scenic": round(scenic_component, 1),
                "poi": round(poi_component, 1),
                "curvature": round(curvature_component, 1),
                "secondary_roads": round(secondary_component, 1),
            },
        }

        logger.debug(f"Calculated scenic metrics: {metrics['total_scenic_score']}/100")
        return metrics

    def _calculate_scenic_route_basic(
        self, start_vertex: int, end_vertex: int, force_secondary: bool = False
    ) -> list[int] | None:
        """Calculate base scenic route."""
        if force_secondary:
            cost_column = self.get_secondary_cost_column()
        else:
            cost_column = self.get_cost_column()

        cache_key = (start_vertex, end_vertex, self.preference, force_secondary)

        if cache_key in self._route_cache:
            logger.debug(
                f"Returning basic route from cache: {start_vertex}->{end_vertex}"
            )
            return self._route_cache[cache_key]

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
                self._route_cache[cache_key] = edge_ids
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

    def _check_route_sanity(
        self, segments: list[dict], start_point: Point, end_point: Point
    ) -> tuple[bool, str]:
        """Check if route is a good route to use."""
        if not segments:
            return False, "Empty route"

        total_distance_m = sum(seg.get("length_m", 0) for seg in segments)
        total_distance_km = total_distance_m / 1000

        start_wkt = f"SRID=4326;POINT({start_point.x} {start_point.y})"
        end_wkt = f"SRID=4326;POINT({end_point.x} {end_point.y})"

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT ST_Distance(
                    ST_GeomFromEWKT(%s)::geography,
                    ST_GeomFromEWKT(%s)::geography
                ) / 1000.0
                """,
                [start_wkt, end_wkt],
            )
            straight_line_km = cursor.fetchone()[0]

        if straight_line_km == 0:
            return True, "Start and end points are identical"

        circuitous_factor = total_distance_km / straight_line_km

        if circuitous_factor > self.MAX_CIRCUITOUS_FACTOR:
            return (
                False,
                f"Route too circuitous (factor: {circuitous_factor:.2f}, "
                f"max: {self.MAX_CIRCUITOUS_FACTOR})",
            )

        return True, f"Route reasonable (circuitous factor: {circuitous_factor:.2f})"

    def calculate_route(
        self, start_point: Point, end_point: Point, **kwargs
    ) -> dict | None:
        """Calculate route."""
        start_time = time.time()
        vertex_threshold = kwargs.get("vertex_threshold", self.DEFAULT_VERTEX_THRESHOLD)
        reference_fastest_time = kwargs.get("reference_fastest_time")
        max_time_excess_minutes = kwargs.get(
            "max_time_excess_minutes", self.MAX_TIME_EXCESS_MINUTES
        )
        force_secondary_routes = kwargs.get("force_secondary_routes", False)

        logger.info(
            f"Starting scenic route calculation ({self.preference}) "
            f"from {start_point} to {end_point}"
        )

        start_vertex = _find_nearest_vertex(start_point, vertex_threshold)
        end_vertex = _find_nearest_vertex(end_point, vertex_threshold)

        if not start_vertex or not end_vertex:
            logger.warning(
                f"Cannot find road vertices: start={start_vertex}, end={end_vertex}"
            )
            return None
        logger.debug(f"Found vertices: start={start_vertex}, end={end_vertex}")

        try:
            basic_edges = self._calculate_scenic_route_basic(
                start_vertex, end_vertex, force_secondary_routes
            )
            if not basic_edges:
                logger.warning("No basic scenic route found")
                return None

            basic_segments = _get_segments_by_ids(basic_edges)
            if not basic_segments:
                logger.warning("Cannot retrieve basic route segments")
                return None
            logger.debug(f"Basic route has {len(basic_segments)} segments")

            is_sane, sanity_message = self._check_route_sanity(
                basic_segments, start_point, end_point
            )
            if not is_sane:
                logger.warning(f"Route sanity check failed: {sanity_message}")

                if not force_secondary_routes:
                    logger.info("Trying again with secondary road preference")
                    return self.calculate_route(
                        start_point=start_point,
                        end_point=end_point,
                        reference_fastest_time=reference_fastest_time,
                        vertex_threshold=vertex_threshold,
                        force_secondary_routes=True,
                    )
                else:
                    logger.warning("Even secondary route failed sanity check")
                    return None

            basic_metrics = _calculate_path_metrics(basic_segments)
            basic_time = basic_metrics.get("total_time_minutes", 0)
            logger.debug(f"Basic route time: {basic_time:.1f} min")

            pois = self._find_pois_along_route(basic_segments)
            logger.info(f"Identified {len(pois)} potential POIs")

            if pois:
                route_edges, included_pois = self._build_route_through_pois(
                    start_vertex,
                    end_vertex,
                    pois,
                    reference_fastest_time,
                    max_time_excess_minutes,
                    basic_time,
                    force_secondary_routes,
                )

                if route_edges:
                    is_sane_poi, poi_sanity_message = self._check_route_sanity(
                        _get_segments_by_ids(route_edges), start_point, end_point
                    )
                    if not is_sane_poi:
                        logger.warning(
                            f"POI route sanity check failed: {poi_sanity_message}"
                        )
                        route_edges = basic_edges
                        included_pois = []
                else:
                    route_edges = basic_edges
                    included_pois = []
            else:
                route_edges = basic_edges
                included_pois = []
                logger.info("No valid POIs found, using basic scenic route")

            final_segments = _get_segments_by_ids(route_edges)
            if not final_segments:
                logger.warning("Cannot retrieve final route segments")
                return None

            route_metrics = _calculate_path_metrics(final_segments)
            scenic_metrics = self._calculate_route_scenic_metrics(final_segments)

            route_geometry = _create_route_geometry(final_segments)
            polyline_encoded = _encode_linestring_to_polyline(route_geometry)

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

            processing_time = time.time() - start_time

            result = {
                "route_type": "scenic",
                "preference": self.preference,
                "preference_description": self.config["description"],
                "start_vertex": start_vertex,
                "end_vertex": end_vertex,
                **route_metrics,
                **scenic_metrics,
                "polyline": polyline_encoded,
                "geometry": route_geometry,
                "segments": final_segments[:10],
                "total_segments": len(final_segments),
                "poi_stops": [poi.to_dict() for poi in included_pois],
                "poi_count": len(included_pois),
                "time_constraint": {
                    "max_excess_minutes": max_time_excess_minutes,
                    "actual_excess_minutes": round(time_excess_minutes, 1),
                    "is_within_constraint": is_within_constraint,
                    "reference_fastest_minutes": reference_fastest_time,
                },
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
                "processing_time_ms": round(processing_time * 1000, 2),
                "cache_hits": len(self._route_cache),
                "used_secondary_preference": force_secondary_routes,
                "route_sanity_check": sanity_message,
            }

            logger.info(
                f"Scenic route complete: "
                f"{scenic_metrics['total_scenic_score']}/100 scenic, "
                f"{len(included_pois)} POIs, "
                f"{route_metrics['total_distance_km']:.1f}km, "
                f"{route_metrics['total_time_minutes']:.0f}min, "
                f"processed in {processing_time:.2f}s"
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
        force_secondary: bool = False,
    ) -> tuple[list[int], list[POIStop]]:
        """Build route using POIs."""
        sorted_pois = sorted(pois, key=lambda p: p.scenic_value, reverse=True)

        min_pois = self.config["min_pois"]
        max_pois = min(self.config["max_pois"], len(sorted_pois))

        logger.debug(
            f"Trying to include {min_pois}-{max_pois} POIs from {len(pois)} candidates"
        )

        basic_edges = self._calculate_scenic_route_basic(
            start_vertex, end_vertex, force_secondary
        )
        if not basic_edges:
            return [], []

        best_route_edges = basic_edges
        best_pois = []
        best_score = 0.0

        route_cache = {}
        vertex_cache = {}

        for poi_count in range(min_pois, max_pois + 1):
            selected_pois = sorted_pois[:poi_count]
            logger.debug(
                f"Trying route with {poi_count} POIs: {[p.name for p in selected_pois]}"
            )

            try:
                route_edges = []
                included_pois = []
                current_vertex = start_vertex

                for poi in selected_pois:
                    poi_key = (poi.location.x, poi.location.y)
                    if poi_key not in vertex_cache:
                        vertex_cache[poi_key] = _find_nearest_vertex(
                            poi.location, distance_threshold=0.01
                        )

                    poi_vertex = vertex_cache[poi_key]

                    if not poi_vertex:
                        logger.debug(f"Cannot find vertex near POI: {poi.name}")
                        continue

                    segment_key = (current_vertex, poi_vertex, force_secondary)
                    if segment_key not in route_cache:
                        route_cache[segment_key] = self._calculate_scenic_route_basic(
                            current_vertex, poi_vertex, force_secondary
                        )

                    segment_edges = route_cache[segment_key]

                    if not segment_edges:
                        logger.debug(f"No route to POI: {poi.name}")
                        break

                    route_edges.extend(segment_edges)
                    included_pois.append(poi)
                    current_vertex = poi_vertex

                if not included_pois:
                    continue

                final_key = (current_vertex, end_vertex, force_secondary)
                if final_key not in route_cache:
                    route_cache[final_key] = self._calculate_scenic_route_basic(
                        current_vertex, end_vertex, force_secondary
                    )

                final_segment = route_cache[final_key]

                if not final_segment:
                    continue

                route_edges.extend(final_segment)

                segments = _get_segments_by_ids(route_edges)
                if not segments:
                    continue

                metrics = _calculate_path_metrics(segments)
                route_time = metrics["total_time_minutes"]

                detour_factor = (
                    route_time / basic_route_time if basic_route_time > 0 else 1.0
                )

                time_ok = True
                if reference_fastest_time:
                    time_excess = route_time - reference_fastest_time
                    time_ok = time_excess <= max_time_excess_minutes

                detour_ok = detour_factor <= self.MAX_DETOUR_FACTOR

                scenic_metrics = self._calculate_route_scenic_metrics(segments)
                route_score = scenic_metrics["total_scenic_score"]

                if time_ok and detour_ok and route_score > best_score:
                    best_score = route_score
                    best_route_edges = route_edges
                    best_pois = included_pois
                    logger.debug(f"New best route found with score {route_score:.1f}")

            except Exception as e:
                logger.debug(f"Error building route with {poi_count} POIs: {str(e)}")
                continue

        if best_pois:
            logger.info(
                f"Selected optimal route with {len(best_pois)} POIs, "
                f"scenic score: {best_score:.1f}"
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
        """Calculate the scenic route."""
        for coord_name, lat, lon in [
            ("start", start_lat, start_lon),
            ("end", end_lat, end_lon),
        ]:
            is_valid, error_msg = _validate_coordinates(lat, lon)
            if not is_valid:
                raise ValueError(f"Invalid {coord_name} coordinates: {error_msg}")

        start_point = Point(start_lon, start_lat, srid=4326)
        end_point = Point(end_lon, end_lat, srid=4326)

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
        """Calculate fastest route for reference."""
        logger.info("Calculating scenic route with fastest reference")

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

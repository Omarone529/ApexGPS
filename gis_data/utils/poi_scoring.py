from django.contrib.gis.db import models

# Category weights for different POI types
CATEGORY_WEIGHTS = {
    "panoramic": 3.0,
    "mountain_pass": 3.5,
    "twisty_road": 4.0,
    "viewpoint": 3.0,
    "lake": 2.5,
    "waterfall": 2.8,
    "castle": 2.0,
    "vineyard": 1.8,
    "church": 1.5,
    "historic": 1.8,
    "museum": 1.5,
    "restaurant": 1.2,
    "food": 1.2,
    "default": 1.0,
}


def calculate_poi_scenic_value(
        category: str,
        importance_score: float,
        segment_count: int,
        distance_m: float = 0.0,
        max_distance_m: float = 800.0,
) -> float:
    """Calculate scenic value for a POI based on multiple factors."""

    # Get base weight from category
    base_weight = CATEGORY_WEIGHTS.get(category, CATEGORY_WEIGHTS["default"])

    # Proximity factor: more segments near POI = more important
    # More segments means the POI is accessible from multiple roads
    proximity_factor = min(segment_count / 3.0, 2.0)

    # Distance penalty: closer is better
    # Linear penalty up to 50% reduction at max distance
    distance_penalty = 1.0 - min(distance_m / max_distance_m, 0.5)

    # Calculate final value
    scenic_value = (
            base_weight * importance_score * proximity_factor * distance_penalty
    )

    return round(scenic_value, 2)


def get_poi_scoring_summary(poi_id: int, relations_query=None) -> dict:
    """
    Get a summary of scoring factors for a specific POI.
    Useful for debugging and analysis.
    """
    from gis_data.models import RoadSegmentPOIRelation

    if relations_query is None:
        relations = RoadSegmentPOIRelation.objects.filter(poi_id=poi_id)
    else:
        relations = relations_query.filter(poi_id=poi_id)

    if not relations.exists():
        return {"error": "No relations found for this POI"}

    first_rel = relations.first()
    segment_count = relations.count()

    return {
        "poi_id": poi_id,
        "poi_name": first_rel.poi.name,
        "category": first_rel.poi.category,
        "importance_score": first_rel.poi.importance_score,
        "segment_count": segment_count,
        "avg_distance": relations.aggregate(avg=models.Avg('distance_m'))['avg'],
        "scenic_value_cache": first_rel.scenic_value_cache,
        "weight_breakdown": {
            "base_weight": CATEGORY_WEIGHTS.get(first_rel.poi.category, 1.0),
            "proximity_factor": min(segment_count / 3.0, 2.0),
            "distance_penalty": 1.0 - min(first_rel.distance_m / 800.0, 0.5),
        }
    }
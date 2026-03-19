import os
import requests
import logging
from math import radians, cos, sin, asin, sqrt
from django.core.cache import cache

logger = logging.getLogger(__name__)


class AzureMapsService:
    """
    Service class for interacting with Azure Maps API.
    Only fetches photos for existing POIs in the database.
    """

    def __init__(self):
        self.subscription_key = os.environ.get('AZURE_MAPS_SUBSCRIPTION_KEY')
        self.base_url = os.environ.get('AZURE_MAPS_BASE_URL')

        if not self.subscription_key:
            logger.error("AZURE_MAPS_SUBSCRIPTION_KEY not set in environment variables")
        else:
            logger.info("Azure Maps Service initialized successfully")

    def is_configured(self):
        """Check if subscription key is set."""
        return bool(self.subscription_key)

    def get_poi_photos(self, poi, max_photos=5, max_width=800):
        """
        Get photos for a specific PointOfInterest object using Azure Maps.
        Only searches for photos of the exact POI (name + coordinates).

        Args:
            poi: PointOfInterest instance (from DB)
            max_photos: Maximum number of photos to return
            max_width: Maximum width for photos

        Returns:
            dict: {
                'photos': list of photo objects,
                'source': 'azure_maps',
                'configured': boolean,
                'wikipedia_description': description from POI (for compatibility)
            }
        """
        # Cache key based on POI id
        cache_key = f"azure_poi_photos_{poi.id}"
        cached = cache.get(cache_key)
        if cached:
            logger.debug(f"Cache hit for POI {poi.id} - {poi.name}")
            return cached

        # Check configuration
        if not self.is_configured():
            logger.error(f"Cannot fetch photos for POI {poi.id}: Azure Maps not configured")
            result = {
                'photos': [],
                'source': 'none',
                'configured': False,
                'error': 'Azure Maps not configured',
                'wikipedia_description': poi.description or ''
            }
            cache.set(cache_key, result, timeout=3600)
            return result

        # Search for the specific POI by name and coordinates
        result = self._search_specific_poi(poi, max_photos, max_width)

        if result and result.get('photos'):
            result['wikipedia_description'] = poi.description or ''
            cache.set(cache_key, result, timeout=86400)  # 24 hours
            return result

        # No photos found
        empty_result = {
            'photos': [],
            'source': 'azure_maps',
            'configured': True,
            'wikipedia_description': poi.description or ''
        }
        cache.set(cache_key, empty_result, timeout=3600)  # 1 hour for empty results
        return empty_result

    def _search_specific_poi(self, poi, max_photos, max_width):
        """
        Search for a specific POI using name and coordinates.
        Uses Fuzzy Search with strict parameters to find the exact match.
        """
        # Build precise query
        query = poi.name
        if poi.region:
            query += f", {poi.region}"
        query += ", Italia"

        url = f"{self.base_url}/search/fuzzy/json"
        params = {
            'api-version': '1.0',
            'query': query,
            'lat': poi.location.y,
            'lon': poi.location.x,
            'radius': 1000,  # 1km radius - stricter
            'limit': 3,  # Few results, only the most relevant
            'subscription-key': self.subscription_key
        }

        try:
            response = requests.get(url, params=params, timeout=5)
            if response.status_code == 200:
                data = response.json()
                results = data.get('results', [])

                if results:
                    # Find the best match based on distance and name similarity
                    best_match = self._find_exact_match(results, poi)
                    if best_match:
                        return self._get_place_photos(best_match.get('id'), max_photos, max_width)

        except requests.exceptions.RequestException as e:
            logger.error(f"Azure Maps search error for POI {poi.id}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error for POI {poi.id}: {e}")

        return None

    def _find_exact_match(self, results, poi):
        """
        Find exact match based on:
        - Proximity to coordinates (within 200m)
        - Name similarity
        """
        poi_lat = poi.location.y
        poi_lon = poi.location.x
        poi_name_lower = poi.name.lower()

        best_match = None
        best_score = 0

        for result in results:
            # Get result coordinates
            result_pos = result.get('position', {})
            if not result_pos:
                continue

            result_lat = result_pos.get('lat')
            result_lon = result_pos.get('lon')

            if not result_lat or not result_lon:
                continue

            # Calculate distance (in km)
            lat1, lon1, lat2, lon2 = map(radians, [poi_lat, poi_lon, result_lat, result_lon])
            dlon = lon2 - lon1
            dlat = lat2 - lat1
            a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
            c = 2 * asin(sqrt(a))
            distance_km = 6371 * c

            # If beyond 500m, skip
            if distance_km > 0.5:
                continue

            # Calculate name similarity score
            result_name = result.get('name', '').lower()
            if poi_name_lower in result_name or result_name in poi_name_lower:
                score = 100 - (distance_km * 200)  # Closer = higher score
                if result_name == poi_name_lower:
                    score += 50  # Bonus for exact match

                if score > best_score:
                    best_score = score
                    best_match = result

        return best_match

    def _get_place_photos(self, place_id, max_photos, max_width):
        """
        Get place details including photos.
        API: https://docs.microsoft.com/rest/api/maps/search/get-search-poi-details
        """
        url = f"{self.base_url}/search/poi/details/json"
        params = {
            'api-version': '1.0',
            'placeId': place_id,
            'subscription-key': self.subscription_key
        }

        try:
            response = requests.get(url, params=params, timeout=5)
            if response.status_code == 200:
                data = response.json()

                # Process photos
                photos = []
                photo_urls = data.get('photos', [])

                for idx, photo_url in enumerate(photo_urls[:max_photos]):
                    # Create thumbnail by reducing maxWidth
                    thumbnail_url = photo_url.replace('maxWidth=800',
                                                      'maxWidth=400') if 'maxWidth=' in photo_url else photo_url

                    photos.append({
                        'id': f"azure_{place_id}_{idx}",
                        'url': photo_url,
                        'thumbnail': thumbnail_url,
                        'width': max_width,
                        'source': 'Azure Maps'
                    })

                return {
                    'photos': photos,
                    'source': 'azure_maps',
                    'configured': True
                }

        except requests.exceptions.RequestException as e:
            logger.error(f"Azure Maps place details error: {e}")

        return None


# Singleton instance
azure_maps_service = AzureMapsService()
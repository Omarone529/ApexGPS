import os

import requests
import logging
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


class GooglePlacesService:
    """
    Service class for interacting with Google Places API.
    Provides methods to fetch photos for Points of Interest.
    """

    def __init__(self):
        self.api_key = settings.GOOGLE_PLACES_API_KEY
        self.base_url = os.environ.get('GOOGLE_PLACE_BASE_URL')
        self.photo_url = os.environ.get('GOOGLE_PLACE_PHOTO_URL')

        if not self.api_key:
            logger.warning("GOOGLE_PLACES_API_KEY not set in environment variables")

    def get_poi_photos(self, poi, max_photos=5, max_width=800):
        """Get photos for a PointOfInterest object."""
        cache_key = f"google_poi_photos_{poi.id}_{int(poi.updated_at.timestamp())}"
        cached = cache.get(cache_key)
        if cached:
            logger.debug(f"Cache hit for POI {poi.id} - {poi.name}")
            return cached

        # Try to find by place name first (more accurate)
        if poi.name:
            result = self._search_by_name(poi)
            if result and result.get('photos'):
                cache.set(cache_key, result, timeout=86400)  # 24 hours
                return result

        result = self._search_by_coordinates(poi, max_photos, max_width)
        if result and result.get('photos'):
            cache.set(cache_key, result, timeout=86400)
            return result

        # Return empty result if no photos found
        empty_result = {
            'photos': [],
            'place_details': None,
            'source': 'google_places'
        }
        cache.set(cache_key, empty_result, timeout=3600)
        return empty_result

    def _search_by_name(self, poi):
        """
        Search for a place by name first.
        Uses Text Search API with name and region.
        """

        search_query = poi.name
        if poi.region:
            search_query += f", {poi.region}"
        search_query += ", Italia"

        url = f"{self.base_url}/textsearch/json"
        params = {
            'query': search_query,
            'key': self.api_key
        }

        try:
            response = requests.get(url, params=params, timeout=5)
            if response.status_code == 200:
                data = response.json()
                results = data.get('results', [])

                if results:
                    place = results[0]
                    return self._process_place_result(place)

        except requests.exceptions.RequestException as e:
            logger.error(f"Google Places text search error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in _search_by_name: {e}")

        return None

    def _search_by_coordinates(self, poi, max_photos, max_width):
        """
        Search for nearby places using coordinates.
        Useful when name search fails.
        """
        lat = poi.location.y
        lon = poi.location.x

        url = f"{self.base_url}/nearbysearch/json"
        params = {
            'location': f"{lat},{lon}",
            'radius': 200,
            'key': self.api_key
        }

        try:
            response = requests.get(url, params=params, timeout=5)
            if response.status_code == 200:
                data = response.json()
                results = data.get('results', [])

                if results:
                    # Try to find best match by name similarity
                    best_match = self._find_best_match(results, poi.name)
                    if best_match:
                        return self._process_place_result(best_match, max_photos, max_width)

                    # If no good match, take the closest one
                    return self._process_place_result(results[0], max_photos, max_width)

        except requests.exceptions.RequestException as e:
            logger.error(f"Google Places nearby search error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in _search_by_coordinates: {e}")

        return None

    def _find_best_match(self, places, poi_name):
        """Find the best matching place from a list based on name similarity."""

        if not poi_name:
            return places[0] if places else None

        poi_name_lower = poi_name.lower()
        best_score = 0
        best_place = None

        for place in places:
            place_name = place.get('name', '').lower()

            # Calculate simple similarity score
            if poi_name_lower in place_name or place_name in poi_name_lower:
                # More points for exact match or high word overlap
                poi_words = set(poi_name_lower.split())
                place_words = set(place_name.split())
                common_words = poi_words & place_words

                score = len(common_words)
                if place_name == poi_name_lower:
                    score += 10  # Bonus for exact match

                if score > best_score:
                    best_score = score
                    best_place = place

        return best_place or (places[0] if places else None)

    def _process_place_result(self, place, max_photos=5, max_width=800):
        """Process a Google Places result and fetch photos."""

        place_id = place.get('place_id')
        if not place_id:
            return None

        # Get place details with photos
        details = self._get_place_details(place_id)
        if not details:
            return None

        # Process photos
        photos = []
        for idx, photo in enumerate(details.get('photos', [])[:max_photos]):
            photo_reference = photo.get('photo_reference')
            if photo_reference:
                photo_url = f"{self.photo_url}?maxwidth={max_width}&photoreference={photo_reference}&key={self.api_key}"
                thumbnail_url = f"{self.photo_url}?maxwidth=400&photoreference={photo_reference}&key={self.api_key}"

                photos.append({
                    'id': f"google_{photo_reference[:20]}_{idx}",
                    'url': photo_url,
                    'thumbnail': thumbnail_url,
                    'width': photo.get('width', max_width),
                    'height': photo.get('height'),
                    'source': 'Google Places'
                })

        return {
            'photos': photos,
            'place_details': {
                'place_id': place_id,
                'name': details.get('name'),
                'formatted_address': details.get('formatted_address'),
                'rating': details.get('rating'),
                'user_ratings_total': details.get('user_ratings_total'),
            },
            'source': 'google_places'
        }

    def _get_place_details(self, place_id):
        """Get detailed information about a place including photos."""
        url = f"{self.base_url}/details/json"
        params = {
            'place_id': place_id,
            'fields': 'name,formatted_address,photos,rating,user_ratings_total',
            'key': self.api_key
        }

        try:
            response = requests.get(url, params=params, timeout=5)
            if response.status_code == 200:
                data = response.json()
                return data.get('result')
        except requests.exceptions.RequestException as e:
            logger.error(f"Google Places details error: {e}")

        return None

google_places_service = GooglePlacesService()
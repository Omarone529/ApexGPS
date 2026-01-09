import logging
import os

import requests
from django.contrib.gis.geos import Point

logger = logging.getLogger(__name__)

__all__ = [
    "GeocodingService",
]


class GeocodingService:
    """Service for geocoding location names to coordinates using Nominatim."""

    @classmethod
    def _get_nominatim_url(cls) -> str:
        """
        Get Nominatim URL from environment variable.
        Returns a cleaned URL string.
        """
        nominatim_url = os.environ.get("NOMINATIM_URL")

        nominatim_url = nominatim_url.strip()
        # Remove quotes
        if (
            nominatim_url.startswith('"')
            and nominatim_url.endswith('"')
            or nominatim_url.startswith("'")
            and nominatim_url.endswith("'")
        ):
            nominatim_url = nominatim_url[1:-1]

        nominatim_url = nominatim_url.strip()
        nominatim_url = nominatim_url.rstrip("/")

        # Remove /search suffix if present
        if nominatim_url.endswith("/search"):
            nominatim_url = nominatim_url[:-6]

        return nominatim_url

    @classmethod
    def geocode_location(
        cls, location_name: str, country_code: str = "it"
    ) -> Point | None:
        """Convert location name to coordinates using Nominatim."""
        try:
            base_url = cls._get_nominatim_url()

            params = {
                "q": location_name,
                "format": "json",
                "limit": 1,
                "countrycodes": country_code,
                "accept-language": "it",
            }

            headers = {"User-Agent": "ApexGPS/1.0"}
            search_url = f"{base_url}/search"
            response = requests.get(
                search_url, params=params, headers=headers, timeout=15
            )

            if response.status_code != 200:
                logger.error(f"Geocoding failed with status {response.status_code}")
                return None

            response.raise_for_status()
            data = response.json()

            if data and len(data) > 0:
                location = data[0]
                lat = float(location["lat"])
                lon = float(location["lon"])

                logger.info(f"Geocoded '{location_name}' to {lat}, {lon}")
                return Point(lon, lat)

            logger.warning(f"No results found for location: '{location_name}'")
            return None

        except requests.exceptions.RequestException as e:
            logger.error(f"Geocoding request failed: {str(e)}")
            return None
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Invalid geocoding response: {str(e)}")
            return None

    @classmethod
    def reverse_geocode(cls, point: Point) -> str | None:
        """Convert coordinates to location name."""
        try:
            base_url = cls._get_nominatim_url()

            params = {
                "lat": point.y,
                "lon": point.x,
                "format": "json",
                "accept-language": "it",
            }

            headers = {"User-Agent": "ApexGPS/1.0"}
            reverse_url = f"{base_url}/reverse"

            response = requests.get(
                reverse_url, params=params, headers=headers, timeout=15
            )

            if response.status_code == 200:
                data = response.json()
                if "display_name" in data:
                    return data["display_name"]

            return None

        except requests.exceptions.RequestException as e:
            logger.error(f"Reverse geocoding failed: {str(e)}")
            return None

    @classmethod
    def geocode_batch(cls, location_names: list[str]) -> dict[str, Point]:
        """Geocode multiple locations in batch."""
        results = {}

        for location_name in location_names:
            point = cls.geocode_location(location_name)
            if point:
                results[location_name] = point

        return results

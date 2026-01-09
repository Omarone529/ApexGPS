import logging
import os

import requests
from django.contrib.gis.geos import Point

logger = logging.getLogger(__name__)


class GeocodingService:
    """Service for geocoding location names to coordinates using Nominatim."""

    NOMINATIM_URL = os.environ.get("NOMINATIM_URL")

    @classmethod
    def geocode_location(
        cls, location_name: str, country_code: str = "it"
    ) -> Point | None:
        """Convert location name to coordinates using Nominatim."""
        if not cls.NOMINATIM_URL:
            logger.error("NOMINATIM_URL environment variable not set")
            return None

        try:
            params = {
                "q": location_name,
                "format": "json",
                "limit": 1,
                "countrycodes": country_code,
                "accept-language": "it",
            }

            headers = {"User-Agent": "ApexGPS/1.0 (https://yourapp.com)"}

            logger.debug(f"Geocoding location: {location_name}")
            response = requests.get(
                cls.NOMINATIM_URL, params=params, headers=headers, timeout=10
            )
            response.raise_for_status()

            data = response.json()

            if data and len(data) > 0:
                location = data[0]
                lat = float(location["lat"])
                lon = float(location["lon"])

                logger.info(f"Geocoded '{location_name}' to {lat}, {lon}")
                return Point(lon, lat)

            logger.warning(f"No results found for location: {location_name}")
            return None

        except requests.exceptions.RequestException as e:
            logger.error(f"Geocoding request failed for {location_name}: {e}")
            return None
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Invalid geocoding response for {location_name}: {e}")
            return None

    @classmethod
    def reverse_geocode(cls, point: Point) -> str | None:
        """Convert coordinates to location name."""
        if not cls.NOMINATIM_URL:
            logger.error("NOMINATIM_URL environment variable not set")
            return None

        try:
            reverse_url = cls.NOMINATIM_URL.replace("/search", "/reverse")
            params = {
                "lat": point.y,
                "lon": point.x,
                "format": "json",
                "accept-language": "it",
            }

            headers = {"User-Agent": "ApexGPS/1.0 (https://yourapp.com)"}

            response = requests.get(
                reverse_url, params=params, headers=headers, timeout=10
            )
            response.raise_for_status()

            data = response.json()

            if "display_name" in data:
                return data["display_name"]

            return None

        except requests.exceptions.RequestException as e:
            logger.error(f"Reverse geocoding failed for {point}: {e}")
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

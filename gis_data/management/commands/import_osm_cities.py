import logging
import time

from django.core.management.base import BaseCommand
from django.contrib.gis.geos import Point
from django.db import transaction

from gis_data.models import City
from gis_data.utils.osm_utils import OSMAPIClient, OSMConfig

logger = logging.getLogger(__name__)


def _guess_province_code(province: str) -> str | None:
    """Guess province code from name."""
    if not province:
        return None
    # Simple mapping for common provinces
    province_map = {
        "Roma": "RM", "Milano": "MI", "Napoli": "NA", "Torino": "TO",
        "Palermo": "PA", "Bari": "BA", "Catania": "CT", "Firenze": "FI",
        "Bologna": "BO", "Genova": "GE", "Venezia": "VE", "Messina": "ME",
    }
    return province_map.get(province)


class OSMCityImporter:
    """Import Italian cities from OpenStreetMap."""

    def __init__(self, region: str = None, batch_size: int = 100):
        self.region = region
        self.batch_size = batch_size
        self.api_client = OSMAPIClient()
        self.stats = {
            "imported": 0,
            "skipped": 0,
            "errors": 0,
            "regions_processed": []
        }

    def get_bbox_for_region(self, region: str) -> str:
        """Get bounding box for region."""
        bbox = OSMConfig.REGION_BBOXES.get(region.lower())
        if not bbox:
            raise ValueError(f"Unknown region: {region}")
        return f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"

    def import_region(self, region: str) -> dict:
        """Import cities for a single region."""
        logger.info(f"Importing cities for region: {region}")
        bbox = self.get_bbox_for_region(region)

        query = f"""
        [out:json][timeout:180];
        (
          node["place"="city"]({bbox});
          node["place"="town"]({bbox});
          node["place"="village"]({bbox});
          way["place"="city"]({bbox});
          way["place"="town"]({bbox});
          way["place"="village"]({bbox});
          relation["place"="city"]({bbox});
          relation["place"="town"]({bbox});
          relation["place"="village"]({bbox});
        );
        out body geom;
        """

        data = self.api_client.execute_query(query)
        if not data:
            return {"success": False, "error": "No data from OSM"}

        elements = data.get("elements", [])
        logger.info(f"Found {len(elements)} elements in {region}")

        cities = []
        for element in elements:
            city = self._parse_element(element, region)
            if city:
                cities.append(city)

        saved = self._save_cities(cities)

        return {
            "success": True,
            "region": region,
            "found": len(elements),
            "imported": saved,
            "skipped": len(cities) - saved
        }

    def _parse_element(self, element: dict, region: str) -> City | None:
        """Parse OSM element into City object."""
        try:
            osm_id = element.get("id")
            tags = element.get("tags", {})

            # Get name
            name = tags.get("name:it") or tags.get("name") or tags.get("int_name")
            if not name:
                return None

            # Skip if already exists
            if City.objects.filter(osm_id=osm_id).exists():
                self.stats["skipped"] += 1
                return None

            # Get coordinates
            lat = element.get("lat")
            lon = element.get("lon")

            # For ways/relations, get center
            if not lat and "center" in element:
                lat = element["center"]["lat"]
                lon = element["center"]["lon"]

            if not lat or not lon:
                return None

            # Get population
            population = tags.get("population")
            if population:
                try:
                    population = int(population)
                except (ValueError, TypeError):
                    population = None

            # Get province from tags
            province = tags.get("addr:province") or tags.get("province")
            province_code = tags.get("ref:province") or _guess_province_code(province)

            # Get geometry for ways/relations
            if "geometry" in element and element["type"] != "node":
                # Simplified: we'll store just the point for now
                # Full boundaries would require more complex processing
                pass

            return City(
                name=name,
                province=province,
                province_code=province_code,
                region=region,
                location=Point(float(lon), float(lat), srid=4326),
                population=population,
                osm_id=osm_id,
                is_active=True
            )

        except Exception as e:
            logger.error(f"Error parsing element: {e}")
            self.stats["errors"] += 1
            return None

    def _save_cities(self, cities: list) -> int:
        """Save cities to database."""
        if not cities:
            return 0

        try:
            with transaction.atomic():
                created = City.objects.bulk_create(
                    cities,
                    ignore_conflicts=True,
                    batch_size=self.batch_size
                )
            return len(created)
        except Exception as e:
            logger.error(f"Bulk create failed: {e}")
            # Fallback to individual saves
            saved = 0
            for city in cities:
                try:
                    city.save()
                    saved += 1
                except Exception:
                    pass
            return saved

    def import_all_regions(self):
        """Import cities for all Italian regions."""
        regions = OSMConfig.ALL_REGIONS
        results = []

        for i, region in enumerate(regions):
            logger.info(f"\n--- Region {i + 1}/{len(regions)}: {region} ---")
            result = self.import_region(region)
            results.append(result)
            self.stats["regions_processed"].append(region)

            if i < len(regions) - 1:
                logger.info("Pausing 5 seconds...")
                time.sleep(5)

        return results


class Command(BaseCommand):
    """Import Italian cities from OpenStreetMap."""

    help = "Import cities, towns and villages from OpenStreetMap"

    def add_arguments(self, parser):
        parser.add_argument(
            "--region",
            type=str,
            help="Specific region to import (e.g., umbria, lazio)",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Import all Italian regions",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show detailed output",
        )

    def handle(self, *args, **options):
        if options["verbose"]:
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.INFO)

        importer = OSMCityImporter()

        if options["all"]:
            self.stdout.write("Importing cities for all Italian regions...")
            results = importer.import_all_regions()

            total_imported = sum(r.get("imported", 0) for r in results)
            self.stdout.write(self.style.SUCCESS(f"\n✓ Imported {total_imported} cities total"))

        elif options["region"]:
            self.stdout.write(f"Importing cities for {options['region']}...")
            result = importer.import_region(options["region"])

            if result["success"]:
                self.stdout.write(self.style.SUCCESS(
                    f"✓ Imported {result['imported']} cities in {options['region']}"
                ))
            else:
                self.stdout.write(self.style.ERROR(f"✗ Failed: {result.get('error')}"))
        else:
            self.stdout.write(self.style.WARNING(
                "Specify --region REGION or --all"
            ))
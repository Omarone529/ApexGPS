import subprocess
from pathlib import Path

import numpy as np
import rasterio
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    """Management command to download and load DEM data into PostGIS."""

    help = "Load DEM to PostGIS"

    AREA_BBOXES = {
        "test_small": (8.0, 45.0, 9.0, 46.0),
        "italy_north": (6.6, 44.0, 13.7, 47.1),
    }

    def add_arguments(self, parser):
        """Define command-line arguments for area and table name."""
        parser.add_argument("--area", type=str, required=True)
        parser.add_argument("--table-name", type=str, default="dem")

    def get_bbox_from_area(self, area_name):
        """Return bounding box coordinates for a predefined area."""
        return self.AREA_BBOXES[area_name]

    def create_test_dem(self, bbox, output_path):
        """Generate a synthetic DEM raster file for testing purposes."""
        min_lon, min_lat, max_lon, max_lat = bbox

        data = np.random.rand(100, 100) * 1000
        transform = rasterio.transform.from_origin(
            min_lon, max_lat, (max_lon - min_lon) / 100, (max_lat - min_lat) / 100
        )

        with rasterio.open(
            output_path,
            "w",
            driver="GTiff",
            height=100,
            width=100,
            count=1,
            dtype=data.dtype,
            crs="EPSG:4326",
            transform=transform,
        ) as dst:
            dst.write(data, 1)

        return output_path

    def load_with_raster2pgsql(self, raster_path, table_name):
        """Load DEM raster file into PostGIS using raster2pgsql utility."""
        filename = Path(raster_path).name

        # Install raster2pgsql if needed
        subprocess.run(
            "docker compose exec db apt-get update && "
            "docker compose exec db apt-get install -y postgis",
            shell=True,
            capture_output=True,
        )

        # Load DEM
        cmd = f"""
        docker compose exec db bash -c "
            cd /dem_data &&
            raster2pgsql -s 4326 -I {filename} -t 100x100 {table_name} |
            psql -U postgres -d apexgps_db
        "
        """

        result = subprocess.run(cmd, shell=True, capture_output=True)
        return result.returncode == 0

    def handle(self, *args, **options):
        """Main entry point for the management command execution."""
        area = options["area"]
        table_name = options["table_name"]

        bbox = self.get_bbox_from_area(area)
        dem_dir = Path("/app/dem_data")
        dem_dir.mkdir(exist_ok=True)

        # Check for existing DEM files
        dem_files = list(dem_dir.glob("*.tif")) + list(dem_dir.glob("*.tiff"))

        if not dem_files:
            # Create test DEM
            dem_file = dem_dir / "test_dem.tif"
            self.create_test_dem(bbox, str(dem_file))
        else:
            dem_file = dem_files[0]

        # Load to PostGIS
        self.load_with_raster2pgsql(str(dem_file), table_name)

        # Verify
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {table_name};")

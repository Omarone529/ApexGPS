import logging
import os
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection

logger = logging.getLogger(__name__)

__all__ = [
    "DEMConfig",
    "DEMDataGenerator",
    "RasterToolManager",
    "DatabaseDEMLoader",
    "DEMFileManager",
    "PreparationPipeline",
    "Command",
]


class DEMConfig:
    """Configuration for DEM data preparation."""

    @staticmethod
    def get_area_bboxes() -> dict[str, tuple[float, float, float, float]]:
        """Get bounding boxes for predefined areas."""
        return {
            "test_small": (8.0, 45.0, 9.0, 46.0),
            "italy_north": (6.6, 44.0, 13.7, 47.1),
            "rome_area": (12.4, 41.8, 12.6, 42.0),
            "milan_area": (9.1, 45.4, 9.3, 45.6),
            "florence_area": (11.2, 43.7, 11.3, 43.8),
        }

    @staticmethod
    def get_dem_directory() -> Path:
        """Get DEM directory path."""
        return Path("dem_data")

    @staticmethod
    def get_database_config() -> dict[str, str]:
        """Get database configuration from Django settings."""
        db_settings = settings.DATABASES["default"]
        return {
            "name": db_settings["NAME"],
            "user": db_settings["USER"],
            "password": db_settings["PASSWORD"],
            "host": db_settings["HOST"],
            "port": str(db_settings["PORT"]),
        }


class DEMDataGenerator:
    """Generator for DEM raster data."""

    def __init__(self, resolution: int = 100):
        """Initialize the DEMDataGenerator."""
        self.resolution = resolution

    def generate_terrain_data(
        self, bbox: tuple[float, float, float, float]
    ) -> np.ndarray:
        """Generate realistic synthetic terrain data."""
        min_lon, min_lat, max_lon, max_lat = bbox

        # Create coordinate grids
        x = np.linspace(0, 1, self.resolution)
        y = np.linspace(0, 1, self.resolution)
        X, Y = np.meshgrid(x, y)

        # Base parameters in meters
        base_elevation = 200
        mountain_height = 800

        # Calculate distance from center
        center_distance = np.sqrt((X - 0.5) ** 2 + (Y - 0.5) ** 2)

        # Create realistic terrain features
        terrain = (
            base_elevation
            + mountain_height * np.exp(-center_distance * 3)  # Central mountain
            + 150 * np.sin(X * 8) * np.cos(Y * 6)  # Ridges
            + 80 * np.sin(X * 15)  # Smaller ridges
            + np.random.randn(self.resolution, self.resolution) * 40  # Noise
        )

        # Ensure positive elevations
        return np.maximum(terrain, 0)

    def create_dem_file(
        self, bbox: tuple[float, float, float, float], output_path: Path
    ) -> bool:
        """Create a synthetic DEM raster file."""
        try:
            # Generate terrain data
            data = self.generate_terrain_data(bbox)

            # Create transform
            min_lon, min_lat, max_lon, max_lat = bbox
            transform = rasterio.transform.from_origin(
                min_lon,
                max_lat,
                (max_lon - min_lon) / self.resolution,
                (max_lat - min_lat) / self.resolution,
            )

            # Write raster file
            with rasterio.open(
                output_path,
                "w",
                driver="GTiff",
                height=self.resolution,
                width=self.resolution,
                count=1,
                dtype=data.dtype,
                crs="EPSG:4326",
                transform=transform,
                nodata=-9999,
            ) as dst:
                dst.write(data, 1)

            logger.info(f"Created DEM file: {output_path}")
            logger.info(f"  Bounds: {bbox}")
            logger.info(f"  Resolution: {self.resolution}x{self.resolution}")
            logger.info(f"  Elevation: {data.min():.1f} - {data.max():.1f} m")

            return True

        except Exception as e:
            logger.error(f"Failed to create DEM file: {e}")
            return False


class RasterToolManager:
    """Manager for raster tools (raster2pgsql)."""

    @staticmethod
    def is_raster2pgsql_available() -> bool:
        """Check if raster2pgsql utility is available."""
        try:
            result = subprocess.run(
                ["which", "raster2pgsql"], capture_output=True, text=True, check=False
            )
            available = result.returncode == 0
            logger.debug(f"raster2pgsql available: {available}")
            return available
        except Exception as e:
            logger.warning(f"Error checking raster2pgsql: {e}")
            return False

    @staticmethod
    def install_raster2pgsql() -> bool:
        """Install raster2pgsql utility."""
        logger.info("Installing raster2pgsql...")

        try:
            # Try to install postgis wich includes raster2pgsql
            result = subprocess.run(
                "apt-get update && apt-get install -y postgis",
                shell=True,
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode == 0:
                logger.info("✓ raster2pgsql installed successfully")
                return True
            else:
                logger.error(f"✗ Installation failed: {result.stderr}")
                return False

        except Exception as e:
            logger.error(f"✗ Installation error: {e}")
            return False


class DatabaseDEMLoader:
    """Loader for DEM data into PostgreSQL/PostGIS."""

    def __init__(self, db_config: dict[str, str]):
        """Initialize the DatabaseDEMLoader."""
        self.db_config = db_config

    def load_with_raster2pgsql(self, raster_path: Path, table_name: str) -> bool:
        """Load DEM using raster2pgsql utility."""
        if not RasterToolManager.is_raster2pgsql_available():
            logger.warning("raster2pgsql not found, attempting installation...")
            if not RasterToolManager.install_raster2pgsql():
                logger.error("Cannot load DEM: raster2pgsql not available")
                return False

        logger.info(f"Loading DEM with raster2pgsql: {raster_path}")

        try:
            # Build raster2pgsql command
            raster_cmd = [
                "raster2pgsql",
                "-s",
                "4326",  # SRID
                "-I",  # Create spatial index
                "-t",
                "100x100",  # Tile size
                "-F",  # Add filename column
                str(raster_path),
                table_name,
            ]

            # Build psql command
            psql_cmd = [
                "psql",
                "-h",
                self.db_config["host"],
                "-p",
                self.db_config["port"],
                "-U",
                self.db_config["user"],
                "-d",
                self.db_config["name"],
            ]

            # Pipe raster2pgsql output to psql
            process1 = subprocess.Popen(
                raster_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )

            process2 = subprocess.Popen(
                psql_cmd,
                stdin=process1.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "PGPASSWORD": self.db_config["password"]},
            )

            # Wait for completion
            process1.stdout.close()
            stdout, stderr = process2.communicate()

            if process2.returncode == 0:
                logger.info("✓ DEM loaded successfully with raster2pgsql")
                return True
            else:
                logger.error(f"✗ raster2pgsql failed: {stderr.decode()}")
                return False

        except Exception as e:
            logger.error(f"✗ Error in raster2pgsql pipeline: {e}")
            return False

    def load_with_python(self, table_name: str) -> bool:
        """Load DEM using Python/Psycopg (fallback method)."""
        logger.info(f"Creating DEM table with Python: {table_name}")

        try:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE;")

                # Create new table
                create_sql = f"""
                CREATE TABLE {table_name} (
                    rid serial PRIMARY KEY,
                    rast raster,
                    filename text,
                    created_at timestamp DEFAULT CURRENT_TIMESTAMP
                );
                """
                cursor.execute(create_sql)

                # Add spatial index
                index_sql = f"""
                CREATE INDEX idx_{table_name}_rast
                ON {table_name}
                USING gist (ST_ConvexHull(rast));
                """
                cursor.execute(index_sql)

                logger.info(f"Created DEM table: {table_name}")
                return True

        except Exception as e:
            logger.error(f"Failed to create DEM table: {e}")
            return False

    def verify_table(self, table_name: str) -> bool:
        """Verify that DEM table was created successfully."""
        try:
            with connection.cursor() as cursor:
                # Check table exists
                cursor.execute(
                    """
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables
                        WHERE table_schema = 'public'
                        AND table_name = %s
                    )
                """,
                    [table_name],
                )

                exists = cursor.fetchone()[0]

                if exists:
                    # Get row count
                    cursor.execute(f"SELECT COUNT(*) FROM {table_name};")
                    count = cursor.fetchone()[0]
                    logger.info(f"DEM table verified: {count} records")
                    return True
                else:
                    logger.warning("DEM table does not exist")
                    return False

        except Exception as e:
            logger.error(f"Verification failed: {e}")
            return False


class DEMFileManager:
    """Manager for DEM file operations."""

    def __init__(self, dem_dir: Path):
        """Initialize the DEMFileManager."""
        self.dem_dir = dem_dir
        self.dem_dir.mkdir(exist_ok=True)

    def get_existing_dem(self, area: str) -> Path | None:
        """Get existing DEM file for area."""
        pattern = f"{area}_dem.tif"
        dem_file = self.dem_dir / pattern

        if dem_file.exists():
            logger.info(f"Found existing DEM file: {dem_file}")
            return dem_file

        # check for generic names
        for ext in [".tif", ".tiff", ".geotiff"]:
            for file_pattern in [f"*{area}*{ext}", f"*dem*{ext}", f"*{ext}"]:
                files = list(self.dem_dir.glob(file_pattern))
                if files:
                    logger.info(f"Found matching DEM file: {files[0]}")
                    return files[0]

        return None

    def get_dem_info(self, dem_path: Path) -> dict[str, Any]:
        """Get information about DEM file."""
        try:
            with rasterio.open(dem_path) as src:
                data = src.read(1)
                info = {
                    "path": str(dem_path),
                    "size": f"{src.width}x{src.height}",
                    "bounds": src.bounds,
                    "crs": str(src.crs),
                    "elevation_min": float(data.min()),
                    "elevation_max": float(data.max()),
                    "elevation_mean": float(data.mean()),
                }
                logger.debug(f"DEM info: {info}")
                return info
        except Exception as e:
            logger.warning(f"Could not read DEM file info: {e}")
            return {}


class PreparationPipeline:
    """Main pipeline for DEM preparation."""

    def __init__(self, area: str, table_name: str, force: bool = False):
        """Initialize the PreparationPipeline."""
        self.area = area
        self.table_name = table_name
        self.force = force

        self.config = DEMConfig()
        self.file_manager = DEMFileManager(self.config.get_dem_directory())
        self.data_generator = DEMDataGenerator()
        self.db_loader = DatabaseDEMLoader(self.config.get_database_config())

        self.stats = {
            "dem_created": False,
            "dem_loaded": False,
            "dem_verified": False,
            "dem_info": {},
        }

    def validate_area(self) -> tuple[bool, tuple[float, float, float, float] | None]:
        """Validate area name and get bounding box."""
        bboxes = self.config.get_area_bboxes()

        if self.area not in bboxes:
            available = ", ".join(sorted(bboxes.keys()))
            logger.error(f"Unknown area: {self.area}. Available: {available}")
            return False, None

        bbox = bboxes[self.area]
        logger.info(f"Area validated: {self.area} -> {bbox}")
        return True, bbox

    def ensure_dem_file(self, bbox: tuple[float, float, float, float]) -> Path | None:
        """Ensure DEM file exists (create if needed)."""
        # Check for existing file
        existing_dem = self.file_manager.get_existing_dem(self.area)

        if existing_dem and not self.force:
            self.stats["dem_info"] = self.file_manager.get_dem_info(existing_dem)
            logger.info(f"Using existing DEM file: {existing_dem}")
            return existing_dem

        # Create new DEM file
        dem_path = self.file_manager.dem_dir / f"{self.area}_dem.tif"
        logger.info(f"Creating new DEM file: {dem_path}")

        if self.data_generator.create_dem_file(bbox, dem_path):
            self.stats["dem_created"] = True
            self.stats["dem_info"] = self.file_manager.get_dem_info(dem_path)
            return dem_path
        else:
            logger.error("Failed to create DEM file")
            return None

    def load_to_database(self, dem_path: Path) -> bool:
        """Load DEM data to database."""
        logger.info(f"Loading DEM to database table: {self.table_name}")

        # Try raster2pgsql
        if self.db_loader.load_with_raster2pgsql(dem_path, self.table_name):
            self.stats["dem_loaded"] = True
            return True

        # Fallback to Python method
        logger.info("Falling back to Python method...")
        if self.db_loader.load_with_python(self.table_name):
            self.stats["dem_loaded"] = True
            return True

        logger.error("All loading methods failed")
        return False

    def verify_database(self) -> bool:
        """Verify DEM data in database."""
        logger.info("Verifying database...")

        if self.db_loader.verify_table(self.table_name):
            self.stats["dem_verified"] = True
            return True

        logger.warning("Database verification failed")
        return False

    def run(self) -> dict[str, Any]:
        """Run the complete DEM preparation pipeline."""
        result = {"success": False, "error": None, "stats": self.stats.copy()}

        try:
            # Validate area
            is_valid, bbox = self.validate_area()
            if not is_valid:
                result["error"] = f"Invalid area: {self.area}"
                return result

            # Ensure DEM file exists
            dem_path = self.ensure_dem_file(bbox)
            if not dem_path:
                result["error"] = "Failed to create/get DEM file"
                return result

            # Load to database
            if not self.load_to_database(dem_path):
                result["error"] = "Failed to load DEM to database"
                return result

            # Verify database
            self.verify_database()

            result["success"] = True
            result["stats"] = self.stats.copy()

        except Exception as e:
            logger.error(f"Pipeline error: {e}", exc_info=True)
            result["error"] = str(e)

        return result


class Command(BaseCommand):
    """Management command to prepare DEM data."""

    help = "Prepare DEM (Digital Elevation Model) data for scenic routing"

    def add_arguments(self, parser):
        """Define command-line arguments."""
        parser.add_argument(
            "--area",
            type=str,
            required=True,
            help="Area name (test_small, italy_north, rome_area, etc.)",
        )
        parser.add_argument(
            "--table-name",
            type=str,
            default="dem",
            help="Table name for storing DEM data",
        )
        parser.add_argument(
            "--force", action="store_true", help="Recreate DEM even if it exists"
        )
        parser.add_argument(
            "--skip-load",
            action="store_true",
            help="Skip loading to database (just create DEM file)",
        )
        parser.add_argument(
            "--verbose", action="store_true", help="Show detailed output"
        )
        parser.add_argument(
            "--resolution",
            type=int,
            default=100,
            help="DEM resolution (pixels per side)",
        )

    def setup_logging(self, verbose: bool):
        """Setup logging based on verbosity."""
        if verbose:
            logging.basicConfig(level=logging.INFO)
        else:
            logging.basicConfig(level=logging.WARNING)

    def display_header(self, area: str):
        """Display command header."""
        self.stdout.write("=" * 60)
        self.stdout.write(f"DEM DATA PREPARATION - Area: {area}")
        self.stdout.write("=" * 60)

    def display_available_areas(self):
        """Display available areas."""
        bboxes = DEMConfig.get_area_bboxes()
        self.stdout.write(f"\nAvailable areas ({len(bboxes)}):")

        areas_list = sorted(bboxes.keys())
        for i in range(0, len(areas_list), 3):
            line_areas = areas_list[i : i + 3]
            self.stdout.write("  " + "  ".join(f"{area:20}" for area in line_areas))

    def display_dem_info(self, dem_info: dict[str, Any]):
        """Display DEM information."""
        if not dem_info:
            return

        self.stdout.write("\nDEM Information:")
        self.stdout.write(f"  File: {dem_info.get('path', 'Unknown')}")
        self.stdout.write(f"  Size: {dem_info.get('size', 'Unknown')}")

        bounds = dem_info.get("bounds")
        if bounds:
            self.stdout.write(f"  Bounds: {bounds}")

        elev_min = dem_info.get("elevation_min")
        elev_max = dem_info.get("elevation_max")
        if elev_min is not None and elev_max is not None:
            self.stdout.write(f"  Elevation: {elev_min:.1f} - {elev_max:.1f} m")

    def display_results(self, result: dict[str, Any]):
        """Display pipeline results."""
        self.stdout.write("\n" + "=" * 60)

        if result["success"]:
            self.stdout.write(self.style.SUCCESS("DEM PREPARATION SUCCESSFUL"))

            stats = result["stats"]
            self.stdout.write("\nSteps completed:")

            if stats.get("dem_created"):
                self.stdout.write("  ✓ DEM file created")
            else:
                self.stdout.write("  ✓ Using existing DEM file")

            if stats.get("dem_loaded"):
                self.stdout.write("  ✓ DEM loaded to database")

            if stats.get("dem_verified"):
                self.stdout.write("  ✓ Database verified")

            # Display DEM info
            self.display_dem_info(stats.get("dem_info", {}))

        else:
            self.stdout.write(self.style.ERROR("DEM PREPARATION FAILED"))

            if result["error"]:
                self.stdout.write(f"\nError: {result['error']}")

            # Display partial stats
            stats = result["stats"]
            if stats.get("dem_info"):
                self.stdout.write("\nPartial results:")
                self.display_dem_info(stats["dem_info"])

        self.stdout.write("=" * 60)

    def handle(self, *args, **options):
        """Execute the DEM preparation process."""
        self.setup_logging(options["verbose"])
        self.display_header(options["area"])
        self.display_available_areas()
        self.stdout.write(f"\nStarting DEM preparation for area: {options['area']}")

        pipeline = PreparationPipeline(
            area=options["area"],
            table_name=options["table_name"],
            force=options["force"],
        )

        result = pipeline.run()

        self.display_results(result)

        if not result["success"]:
            raise SystemExit(1)

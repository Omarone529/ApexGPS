import gc
import logging
import time

import psutil
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection, reset_queries

from gis_data.models import PointOfInterest, RoadSegment

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Import Italian regions with optimized memory usage."""

    help = "Import all Italian regions with optimized memory usage"

    OPTIMIZED_BATCHES = [
        ["valle-daosta"],
        ["molise"],
        ["umbria"],
        ["marche"],
        ["basilicata"],
        ["abruzzo"],
        ["friuli-venezia-giulia"],
        ["liguria"],
        ["toscana"],
        ["lazio"],
        ["sardegna"],
        ["emilia-romagna"],
        ["piemonte"],
        ["veneto"],
        ["lombardia"],
        ["sicilia"],
        ["puglia"],
        ["calabria"],
        ["campania"],
        ["trentino-alto-adige"],
    ]

    def add_arguments(self, parser):
        """Add command line arguments."""
        parser.add_argument(
            "--skip-existing",
            action="store_true",
            help="Skip regions that already have data",
        )
        parser.add_argument("--skip-pois", action="store_true", help="Skip POI import")
        parser.add_argument(
            "--batch-pause",
            type=int,
            default=10,
            help="Seconds to pause between batches (default: 10)",
        )
        parser.add_argument(
            "--minimal",
            action="store_true",
            help="Load only Umbria region for immediate operation",
        )
        parser.add_argument(
            "--regions-only",
            action="store_true",
            help="Import only regions, skip POIs and GIS prep",
        )
        parser.add_argument(
            "--memory-limit-mb",
            type=int,
            default=1024,
            help="Memory limit in MB before forced cleanup (default: 1024)",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Batch size for processing segments (default: 1000)",
        )

    def _check_memory_usage(self, threshold_mb):
        """Check if memory usage exceeds threshold."""
        process = psutil.Process()
        mem_mb = process.memory_info().rss / 1024 / 1024
        return mem_mb > threshold_mb

    def _force_memory_cleanup(self):
        """Force memory cleanup and cache clearing."""
        reset_queries()
        gc.collect()
        from django.core.cache import cache

        cache.clear()

    def _load_minimal_dataset(self, options):
        """Load minimal dataset (Umbria only)."""
        if self._check_memory_usage(options["memory_limit_mb"]):
            self._force_memory_cleanup()

        call_command("import_osm_roads", regions="umbria", verbose=True)
        self._force_memory_cleanup()

        if not options["regions_only"]:
            self.stdout.write("Preparing routing data...")
            call_command("prepare_gis_data", area="italy", force=True, verbose=True)
            self._force_memory_cleanup()

        if not options["skip_pois"] and not options["regions_only"]:
            self.stdout.write("Importing essential POIs...")
            call_command(
                "import_osm_pois",
                area="umbria",
                categories="viewpoint,restaurant",
                verbose=True,
            )
            self._force_memory_cleanup()

    def _load_full_italy_optimized(self, options):
        """Load all 20 Italian regions in optimized batches."""
        total_regions_imported = 0
        total_segments = 0
        failed_regions = []

        self.stdout.write("=" * 60)
        self.stdout.write("IMPORTING ALL 20 ITALIAN REGIONS")
        self.stdout.write("=" * 60)

        for batch_num, regions in enumerate(self.OPTIMIZED_BATCHES, 1):
            self.stdout.write(f"\nBatch {batch_num}: {', '.join(regions)}")
            batch_start = time.time()
            batch_segments = 0

            for region in regions:
                try:
                    if self._check_memory_usage(options["memory_limit_mb"]):
                        self._force_memory_cleanup()

                    if options["skip_existing"]:
                        existing = RoadSegment.objects.filter(
                            osm_id__isnull=False
                        ).count()
                        if existing > 1000:  # Reasonable threshold
                            self.stdout.write(
                                f"  Skipping {region} ({existing} segments exist)"
                            )
                            total_regions_imported += 1
                            continue

                    self.stdout.write(f"  Importing {region}...")
                    call_command("import_osm_roads", regions=region, verbose=True)
                    self._force_memory_cleanup()

                    count = RoadSegment.objects.filter(osm_id__isnull=False).count()
                    total_segments += count
                    batch_segments += count
                    total_regions_imported += 1

                    self.stdout.write(f"  {count:,} segments imported")

                except Exception as e:
                    error_msg = str(e)[:100]
                    self.stdout.write(f"  Failed: {error_msg}")
                    failed_regions.append(region)
                    continue

            if batch_segments > 0:
                self.stdout.write("  Optimizing database...")
                with connection.cursor() as cursor:
                    cursor.execute("VACUUM ANALYZE gis_data_roadsegment;")

            batch_time = time.time() - batch_start
            self.stdout.write(f"  Batch completed in {batch_time:.1f}s")

            if batch_num < len(self.OPTIMIZED_BATCHES):
                pause_time = options["batch_pause"]
                self.stdout.write(f"  Pausing for {pause_time}s...")
                time.sleep(pause_time)
                self._force_memory_cleanup()

        if not options["regions_only"] and total_segments > 0:
            self.stdout.write("\n" + "=" * 60)
            self.stdout.write("PREPARING ROUTING DATA")
            self.stdout.write("=" * 60)

            gis_start = time.time()
            call_command("prepare_gis_data", area="italy", force=True, verbose=True)
            self._force_memory_cleanup()

            gis_time = time.time() - gis_start
            self.stdout.write(f"GIS preparation completed in {gis_time:.1f}s")

        if not options["skip_pois"] and not options["regions_only"]:
            self.stdout.write("\n" + "=" * 60)
            self.stdout.write("IMPORTING POIs")
            self.stdout.write("=" * 60)

            poi_start = time.time()
            poi_categories = [
                ("viewpoint,restaurant", "essential"),
                ("church,historic", "cultural"),
                ("hospital,police,school", "services"),
                ("hotel,attraction,museum", "tourism"),
            ]

            for categories, category_name in poi_categories:
                self.stdout.write(f"\nImporting {category_name} POIs...")

                if self._check_memory_usage(options["memory_limit_mb"]):
                    self._force_memory_cleanup()

                call_command(
                    "import_osm_pois",
                    area="italy",
                    categories=categories,
                    verbose=True,
                )
                self._force_memory_cleanup()

            poi_time = time.time() - poi_start
            poi_count = PointOfInterest.objects.count()
            self.stdout.write(f"{poi_count:,} POIs imported in {poi_time:.1f}s")

        # Save stats
        self.stats = {
            "total_regions_imported": total_regions_imported,
            "total_segments": total_segments,
            "failed_regions": failed_regions,
        }

    def _print_summary(self, total_time, options):
        """Print import summary."""
        from gis_data.models import PointOfInterest, RoadSegment

        road_count = RoadSegment.objects.count()

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("DATASET LOADING COMPLETE")
        self.stdout.write("=" * 60)

        self.stdout.write(
            f"Total time: {total_time:.1f}s ({total_time / 60:.1f} minutes)"
        )

        if hasattr(self, "stats"):
            successful = self.stats["total_regions_imported"]
            failed = len(self.stats["failed_regions"])
            self.stdout.write(f"Regions imported: {successful}/20")
            if failed > 0:
                self.stdout.write(f"Failed regions: {failed}")

        self.stdout.write(f"Total road segments: {road_count:,}")

        if not options["skip_pois"] and not options["regions_only"]:
            poi_count = PointOfInterest.objects.count()
            self.stdout.write(f"Total POIs: {poi_count:,}")

        self.stdout.write("System ready with complete Italy dataset")
        self.stdout.write("=" * 60)

    def handle(self, *args, **options):
        """Execute the command."""
        start_time = time.time()

        if options["minimal"]:
            self.stdout.write("Loading minimal dataset (Umbria only)...")
            self._load_minimal_dataset(options)
        else:
            self.stdout.write("Loading complete Italy dataset (20 regions)...")
            self._load_full_italy_optimized(options)

        total_time = time.time() - start_time
        self._print_summary(total_time, options)

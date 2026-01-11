import logging
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Import Italian regions with memory optimization.
    Batches organized from smallest to largest regions.
    """

    help = "Import all Italian regions with optimized memory usage"

    # All Italian regions in optimized memory batches
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
        """Add arguments to parser."""
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

    def handle(self, *args, **options):
        """Handle the command start."""
        start_time = time.time()

        if options["minimal"]:
            self.stdout.write("Loading minimal dataset (Umbria only)...")
            self._load_minimal_dataset(options)
        else:
            self.stdout.write("Loading complete Italy dataset (20 regions)...")
            self._load_full_italy(options)

        total_time = time.time() - start_time
        self._print_summary(total_time, options)

    def _load_minimal_dataset(self, options):
        """Load only one region for immediate operation."""
        call_command("import_osm_roads", regions="umbria", verbose=True)

        # Prepare GIS
        if not options["regions_only"]:
            self.stdout.write("Preparing routing data...")
            call_command("prepare_gis_data", area="italy", force=True, verbose=True)

        # POIs
        if not options["skip_pois"] and not options["regions_only"]:
            self.stdout.write("Importing essential POIs...")
            call_command(
                "import_osm_pois",
                area="umbria",
                categories="viewpoint,restaurant",
                verbose=True,
            )

    def _load_full_italy(self, options):
        """Load all 20 Italian regions."""
        total_regions_imported = 0
        total_segments = 0
        failed_regions = []

        self.stdout.write("=" * 60)
        self.stdout.write("IMPORTING ALL 20 ITALIAN REGIONS")
        self.stdout.write("=" * 60)

        # Import roads in optimized batches
        for batch_num, regions in enumerate(self.OPTIMIZED_BATCHES, 1):
            self.stdout.write(f"\nBatch {batch_num}: {', '.join(regions)}")
            batch_start = time.time()
            batch_segments = 0

            for region in regions:
                try:
                    # Check if region already has data
                    if options["skip_existing"]:
                        from gis_data.models import RoadSegment

                        existing = RoadSegment.objects.filter(region=region).count()
                        if existing > 100:
                            self.stdout.write(
                                f"  Skipping {region} ({existing} segments exist)"
                            )
                            total_regions_imported += 1
                            continue

                    # Import the region
                    self.stdout.write(f"  Importing {region}...")

                    call_command("import_osm_roads", regions=region, verbose=True)

                    # Count imported segments
                    from gis_data.models import RoadSegment

                    count = RoadSegment.objects.filter(region=region).count()
                    total_segments += count
                    batch_segments += count
                    total_regions_imported += 1

                    self.stdout.write(f"{count:,} segments")

                except Exception as e:
                    error_msg = str(e)[:100]
                    self.stdout.write(f"Failed: {error_msg}")
                    failed_regions.append(region)
                    continue

            # Optimize database after each batch
            if batch_segments > 0:
                self.stdout.write("  Optimizing database...")
                with connection.cursor() as cursor:
                    cursor.execute("VACUUM ANALYZE;")

            batch_time = time.time() - batch_start
            self.stdout.write(f"  Batch completed in {batch_time:.1f}s")

            # Pause between batches
            if batch_num < len(self.OPTIMIZED_BATCHES):
                pause_time = options["batch_pause"]
                self.stdout.write(f"  Pausing for {pause_time}s...")
                time.sleep(pause_time)

        # Prepare GIS data (if not skipped)
        if not options["regions_only"]:
            self.stdout.write("\n" + "=" * 60)
            self.stdout.write("PREPARING ROUTING DATA")
            self.stdout.write("=" * 60)

            gis_start = time.time()
            call_command("prepare_gis_data", area="italy", force=True, verbose=True)
            gis_time = time.time() - gis_start
            self.stdout.write(f"GIS preparation completed in {gis_time:.1f}s")

        # Import POIs (if not skipped)
        if not options["skip_pois"] and not options["regions_only"]:
            self.stdout.write("\n" + "=" * 60)
            self.stdout.write("IMPORTING POIs")
            self.stdout.write("=" * 60)

            poi_start = time.time()

            self.stdout.write("Importing essential POIs...")
            call_command(
                "import_osm_pois",
                area="italy",
                categories="viewpoint,restaurant",
                verbose=True,
            )

            self.stdout.write("Importing cultural POIs...")
            call_command(
                "import_osm_pois",
                area="italy",
                categories="church,historic",
                verbose=True,
            )

            poi_time = time.time() - poi_start

            from gis_data.models import PointOfInterest

            poi_count = PointOfInterest.objects.count()

            self.stdout.write(f"{poi_count:,} POIs imported in {poi_time:.1f}s")

        # Store stats for summary
        self.stats = {
            "total_regions_imported": total_regions_imported,
            "total_segments": total_segments,
            "failed_regions": failed_regions,
        }

    def _print_summary(self, total_time, options):
        """Print final summary."""
        from gis_data.models import PointOfInterest, RoadSegment

        road_count = RoadSegment.objects.count()

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("DATASET LOADING COMPLETE")
        self.stdout.write("=" * 60)

        self.stdout.write(
            f"\nTotal time: {total_time:.1f}s ({total_time / 60:.1f} minutes)"
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

        self.stdout.write("\nSystem ready with complete Italy dataset")
        self.stdout.write("=" * 60)
